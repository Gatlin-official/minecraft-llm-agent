import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class MinecraftClient:
    def __init__(self, base_url, timeout=35):
        self.base_url = base_url
        self.timeout = timeout

    def _request(self, method, path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode())
        except HTTPError as exc:
            body = exc.read().decode()
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                raise RuntimeError(f"Bot API HTTP {exc.code}: {body}") from exc
        except (URLError, TimeoutError) as exc:
            raise RuntimeError(f"Bot API unavailable: {exc}") from exc

    def health(self):
        return self._request("GET", "/health")

    def state(self):
        return self._request("GET", "/state")

    def act(self, action):
        print(f"[HTTP ACTION] action={action.get('action')}")
        return self._request("POST", "/action", action)

    def requirements(self, item, count=1):
        result = self._request("POST", "/requirements", {"item": item, "count": count})
        if not isinstance(result, dict):
            raise RuntimeError("Requirement API returned a non-object response")
        if result.get("success") is False or result.get("error"):
            raise RuntimeError(
                f"Requirement API rejected {item}: "
                f"{result.get('error') or 'unknown error'}"
            )
        return result

    def stop(self):
        return self._request("POST", "/stop", {})
