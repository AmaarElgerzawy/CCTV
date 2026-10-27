import re

path = "app.py"
with open(path, encoding="utf-8") as f:
    s = f.read()

# Replace all unexpected error patterns
s = s.replace('        logger.error("Unexpected error while reading DVR data: %s", type(exc).__name__)\n        return _error(500, "Unexpected server error.")',
              '        log_unexpected(logger, "Unexpected error while reading DVR data", exc)\n        return _error(500, "Unexpected server error.")')

s = s.replace('        logger.error("Unexpected live-stream error: %s", type(exc).__name__)\n        return _error(500, "Unexpected server error.")',
              '        log_unexpected(logger, "Unexpected live-stream error", exc)\n        return _error(500, "Unexpected server error.")')

s = s.replace('        logger.error("Unexpected lease-renewal error: %s", type(exc).__name__)\n        return _error(500, "Unexpected server error.")',
              '        log_unexpected(logger, "Unexpected lease-renewal error", exc)\n        return _error(500, "Unexpected server error.")')

s = s.replace('        logger.error("Unexpected CGI clock error: %s", type(exc).__name__)\n        return _error(500, "Unexpected server error.")',
              '        log_unexpected(logger, "Unexpected CGI clock error", exc)\n        return _error(500, "Unexpected server error.")')

s = s.replace('        logger.error("Unexpected playback error: %s", type(exc).__name__)\n        return _error(500, "Unexpected server error.")',
              '        log_unexpected(logger, "Unexpected playback error", exc)\n        return _error(500, "Unexpected server error.")')

s = s.replace('        logger.error("Unexpected CGI recordings error: %s", type(exc).__name__)\n        return _error(500, "Unexpected server error.")',
              '        log_unexpected(logger, "Unexpected CGI recordings error", exc)\n        return _error(500, "Unexpected server error.")')

s = s.replace('            "Unexpected storage-health error: %s", type(exc).__name__\n        )',
              '        )\n        log_unexpected(logger, "Unexpected storage-health error", exc)')

with open(path, "w", encoding="utf-8") as f:
    f.write(s)

print("done")
