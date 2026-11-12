"""Bounded no-key Google adapter child. No credentials or raw errors on stdout."""
import json
import ssl
import sys
from urllib.parse import urlsplit


def safe_cause(exc):
    pending, visited = [exc], set()
    while pending and len(visited) < 32:
        item = pending.pop()
        if id(item) in visited:
            continue
        visited.add(id(item))
        if isinstance(item, ssl.SSLCertVerificationError):
            return "certificate_verify_failed"
        pending += [part for part in (getattr(item, "__cause__", None), getattr(item, "__context__", None),
                    getattr(item, "reason", None), *getattr(item, "args", ())) if isinstance(part, BaseException)]
    return {"SSLError": "tls_error", "ProxyError": "proxy_error", "ConnectionError": "connection_error",
            "TooManyRequests": "rate_limit",
            "TranslationNotFound": "translation_not_found", "RequestError": "google_http_error",
            "Timeout": "timeout", "ConnectTimeout": "timeout", "ReadTimeout": "timeout"}.get(type(exc).__name__, "runtime_error")


def main():
    diagnostic = {"http_status": 0, "redirect": False, "redirect_host": "", "certificate_validation": True,
                  "allow_redirects": False, "connect_timeout_s": 5, "read_timeout_s": 12}
    try:
        from importlib.metadata import version
        from deep_translator import GoogleTranslator
        import deep_translator.google as google
        import requests
        request = json.loads(sys.stdin.buffer.read())
        diagnostic["read_timeout_s"] = min(120, max(5, int(request.get("read_timeout_s", 12))))
        diagnostic["deep_translator_version"] = version("deep-translator")
        class Requests:
            @staticmethod
            def get(*args, **kwargs):
                response = requests.get(*args, **kwargs, timeout=(5, diagnostic["read_timeout_s"]), allow_redirects=False)
                diagnostic["http_status"] = response.status_code
                diagnostic["redirect"] = 300 <= response.status_code < 400
                if diagnostic["redirect"]:
                    host = urlsplit(response.headers.get("Location", "")).hostname
                    diagnostic["redirect_host"] = host if host in {"consent.google.com", "translate.google.com", "www.google.com"} else "other"
                return response
        google.requests = Requests
        codes = {"zh": "zh-CN", "zh_hant": "zh-TW"}
        result = GoogleTranslator(source=codes.get(request["source"], request["source"]),
            target=codes.get(request["target"], request["target"])).translate(request["text"])
        print(json.dumps({"text": result, "diagnostic": diagnostic}, ensure_ascii=False))
    except Exception as exc:
        diagnostic["cause"] = "redirect" if diagnostic["redirect"] else safe_cause(exc)
        if type(exc).__name__ in {"Timeout", "ConnectTimeout", "ReadTimeout"}:
            print(json.dumps({"error": "timeout", "diagnostic": diagnostic}))
            return
        if type(exc).__name__ == "TooManyRequests":
            print(json.dumps({"error": "rate_limit", "diagnostic": diagnostic}))
            return
        allowed = {"RequestError", "TranslationNotFound", "SSLError", "ProxyError", "ConnectionError",
                   "ModuleNotFoundError", "ImportError", "PermissionError", "JSONDecodeError"}
        print(json.dumps({"error": "invalid_response" if type(exc).__name__ == "TranslationNotFound" else "network",
                          "cause": type(exc).__name__ if type(exc).__name__ in allowed else "runtime_error",
                          "diagnostic": diagnostic}))


if __name__ == "__main__":
    main()
