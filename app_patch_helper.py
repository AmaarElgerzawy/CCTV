import traceback


def log_unexpected(logger, message: str, exc: Exception) -> None:
    exc_type = type(exc).__name__
    if exc_type in ("NameError", "UnboundLocalError"):
        try:
            logger.error("%s %s: %s", message, exc_type, str(exc))
        except Exception:
            logger.error("%s %s", message, exc_type)
        return
    frames = []
    try:
        tb = exc.__traceback__
        while tb is not None:
            frame = tb.tb_frame
            code = frame.f_code
            frames.append(f"{code.co_filename}:{tb.tb_lineno}:{code.co_name}")
            tb = tb.tb_next
        frame_str = " | ".join(frames) if frames else ""
        if frame_str:
            logger.error("%s %s: %s", message, exc_type, frame_str)
        else:
            logger.error("%s %s", message, exc_type)
    except Exception:
        logger.error("%s %s", message, exc_type)
