import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class LLMClient:
    """
    OpenAI-compatible local LLM client.

    Guarantees that malformed LLM JSON does not immediately kill the
    Minecraft agent loop.

    Handles:
      - normal JSON
      - ```json fenced JSON
      - JSON surrounded by prose
      - truncated JSON
      - transient LLM/API failures
      - empty responses
      - structured content responses
    """

    def __init__(
        self,
        base_url,
        model,
        api_key="local",
        timeout=120,
        max_retries=2,
        retry_delay=0.5,
    ):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self.retry_delay = max(0.0, float(retry_delay))

    # ------------------------------------------------------------------
    # JSON parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_code_fences(content):
        content = content.strip()

        if not content.startswith("```"):
            return content

        lines = content.splitlines()

        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        return "\n".join(lines).strip()

    @staticmethod
    def _extract_json_object(content):
        """
        Extract a JSON object from the model response.

        Supports responses such as:

            {"goal": "...", ...}

        or:

            Here is the plan:
            {"goal": "...", ...}

        or:

            ```json
            {"goal": "...", ...}
            ```
        """

        if not isinstance(content, str):
            raise json.JSONDecodeError(
                "LLM content is not a string",
                str(content),
                0,
            )

        content = content.strip()
        content = LLMClient._strip_code_fences(content)

        # --------------------------------------------------------------
        # Fast path: entire response is JSON.
        # --------------------------------------------------------------

        try:
            parsed = json.loads(content)

            if isinstance(parsed, dict):
                return parsed

            raise json.JSONDecodeError(
                "Expected JSON object",
                content,
                0,
            )

        except json.JSONDecodeError:
            pass

        # --------------------------------------------------------------
        # Find first JSON object.
        # --------------------------------------------------------------

        start = content.find("{")

        if start == -1:
            raise json.JSONDecodeError(
                "No JSON object found in LLM response",
                content,
                0,
            )

        depth = 0
        in_string = False
        escaped = False

        for index in range(start, len(content)):
            char = content[index]

            if in_string:
                if escaped:
                    escaped = False

                elif char == "\\":
                    escaped = True

                elif char == '"':
                    in_string = False

                continue

            if char == '"':
                in_string = True

            elif char == "{":
                depth += 1

            elif char == "}":
                depth -= 1

                if depth == 0:
                    candidate = content[start:index + 1]

                    try:
                        parsed = json.loads(candidate)
                    except json.JSONDecodeError:
                        raise

                    if not isinstance(parsed, dict):
                        raise json.JSONDecodeError(
                            "JSON response must be an object",
                            candidate,
                            0,
                        )

                    return parsed

        # Reaching here normally means the model truncated its response.
        raise json.JSONDecodeError(
            "Incomplete JSON object",
            content,
            start,
        )

    # ------------------------------------------------------------------
    # Response extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _content_from_response(raw):
        if not isinstance(raw, dict):
            raise RuntimeError("LLM response is not a JSON object")

        try:
            choices = raw["choices"]
        except KeyError as exc:
            raise RuntimeError(
                "LLM response missing 'choices'"
            ) from exc

        if not isinstance(choices, list) or not choices:
            raise RuntimeError(
                "LLM response contains no choices"
            )

        try:
            message = choices[0]["message"]
        except (KeyError, TypeError, IndexError) as exc:
            raise RuntimeError(
                "LLM response missing assistant message"
            ) from exc

        content = message.get("content")

        # Some OpenAI-compatible servers return a string.
        if isinstance(content, str):
            content = content.strip()

            if not content:
                raise RuntimeError(
                    "LLM returned empty assistant content"
                )

            return content

        # Some APIs return structured content blocks.
        if isinstance(content, list):
            text_parts = []

            for part in content:
                if isinstance(part, str):
                    text_parts.append(part)

                elif isinstance(part, dict):
                    text = part.get("text")

                    if isinstance(text, str):
                        text_parts.append(text)

            result = "".join(text_parts).strip()

            if result:
                return result

        raise RuntimeError(
            "LLM returned empty or unsupported assistant content"
        )

    # ------------------------------------------------------------------
    # HTTP request
    # ------------------------------------------------------------------

    def _request(self, payload):
        request = Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            with urlopen(
                request,
                timeout=self.timeout,
            ) as response:

                raw_bytes = response.read()

                if not raw_bytes:
                    raise RuntimeError(
                        "LLM server returned an empty HTTP response"
                    )

                try:
                    return json.loads(
                        raw_bytes.decode("utf-8")
                    )

                except json.JSONDecodeError as exc:
                    body = raw_bytes.decode(
                        "utf-8",
                        errors="replace",
                    )

                    raise RuntimeError(
                        "LLM server returned invalid HTTP JSON: "
                        f"{body[:1000]}"
                    ) from exc

        except HTTPError as exc:
            try:
                body = exc.read().decode(
                    "utf-8",
                    errors="replace",
                )
            except Exception:
                body = ""

            raise RuntimeError(
                f"LLM HTTP {exc.code}: {body[:1000]}"
            ) from exc

        except (URLError, TimeoutError) as exc:
            raise RuntimeError(
                f"LLM unavailable: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Main JSON completion
    # ------------------------------------------------------------------

    def complete_json(
        self,
        system,
        user,
        response_schema=None,
    ):
        if response_schema is not None:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "minecraft_decision",
                    "strict": True,
                    "schema": response_schema,
                },
            }
        else:
            response_format = {
                "type": "json_object",
            }

        base_messages = [
            {
                "role": "system",
                "content": system,
            },
            {
                "role": "user",
                "content": user,
            },
        ]

        last_error = None

        total_attempts = self.max_retries + 1

        for attempt in range(total_attempts):

            messages = list(base_messages)

            # ----------------------------------------------------------
            # Retry prompt
            # ----------------------------------------------------------

            if attempt > 0:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "IMPORTANT JSON REPAIR.\n"
                            "Your previous response was invalid or "
                            "incomplete JSON.\n\n"
                            "Return ONLY ONE complete JSON object.\n"
                            "Do not use Markdown.\n"
                            "Do not use ```.\n"
                            "Do not explain anything.\n"
                            "Do not add text before or after the JSON.\n"
                            "Do not truncate the response.\n"
                            "Follow the required schema exactly."
                        ),
                    }
                )

            payload = {
                "model": self.model,

                # Deterministic output is much safer for an action planner.
                "temperature": 0,

                # Prevent truncation of nested Minecraft decisions.
                "max_tokens": 1200,

                "messages": messages,

                "response_format": response_format,
            }

            try:
                raw = self._request(payload)

                content = self._content_from_response(raw)

                try:
                    decision = self._extract_json_object(
                        content
                    )

                    return decision

                except json.JSONDecodeError as exc:
                    last_error = exc

                    print(
                        "\n[LLM] INVALID JSON"
                    )

                    print(
                        f"[LLM] Attempt "
                        f"{attempt + 1}/{total_attempts}"
                    )

                    print(
                        "[LLM] Raw model response:"
                    )

                    print(
                        content[:5000]
                    )

                    print(
                        "[LLM] The response will be retried."
                    )

            except RuntimeError as exc:
                last_error = exc

                print(
                    "\n[LLM] REQUEST ERROR"
                )

                print(
                    f"[LLM] Attempt "
                    f"{attempt + 1}/{total_attempts}"
                )

                print(
                    f"[LLM] {exc}"
                )

            # ----------------------------------------------------------
            # Wait before retrying.
            # ----------------------------------------------------------

            if attempt < self.max_retries:
                time.sleep(
                    self.retry_delay * (attempt + 1)
                )

        # --------------------------------------------------------------
        # All attempts failed.
        # --------------------------------------------------------------

        raise RuntimeError(
            "LLM failed to produce valid JSON after "
            f"{total_attempts} attempts"
        ) from last_error