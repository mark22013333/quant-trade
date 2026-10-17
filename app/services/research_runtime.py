"""研究程序的本機生命週期與資源額度；不變更網路設定。"""
from contextlib import contextmanager
import math
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import tempfile
import threading
import time


_ACTIVE = set()
_ACTIVE_LOCK = threading.Lock()
_RESEARCH_SLOTS = threading.BoundedSemaphore(2)
_PROCESS_SLOTS = threading.BoundedSemaphore(2)
_IO_SLOTS = threading.BoundedSemaphore(4)
MAX_OUTPUT_BYTES = 1_000_000


@contextmanager
def research_execution_slot(key):
    """同一程序內，每個實驗單一執行，最多同時兩個研究。"""
    with _ACTIVE_LOCK:
        if key in _ACTIVE:
            raise ValueError("research_in_progress_conflict")
        if not _RESEARCH_SLOTS.acquire(blocking=False):
            raise ValueError("research_busy_conflict")
        _ACTIVE.add(key)
    try:
        yield
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE.remove(key)
            _RESEARCH_SLOTS.release()


def research_environment():
    # 保留既有代理與 TLS 值；不讓券商／控制台憑證隨父程序環境傳入。
    allowed = {
        "PATH", "HOME", "CODEX_HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "TERM",
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        "http_proxy", "https_proxy", "all_proxy", "no_proxy",
        "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    }
    return {key: value for key, value in os.environ.items() if key in allowed}


def _stop_process(process):
    def terminate(sig):
        try:
            if os.name == "posix":
                os.killpg(process.pid, sig)
            elif process.poll() is None:
                process.terminate() if sig == signal.SIGTERM else process.kill()
        except ProcessLookupError:
            pass
    terminate(signal.SIGTERM)
    try:
        process.wait(timeout=.25)
    except subprocess.TimeoutExpired:
        terminate(signal.SIGKILL)
        process.wait(timeout=.5)
    # 父程序已退出仍可能留下同群組子程序；限於本次 start_new_session 群組。
    if os.name == "posix":
        terminate(signal.SIGKILL)


def run_managed_research_process(command, *, cwd, input, timeout, env, output_path=None, popen=None, capture_stdout=False):
    """逾時或輸出過大即終止本次研究程序群組，並回收父程序。"""
    timeout = float(timeout)
    if not math.isfinite(timeout) or timeout <= 0:
        raise subprocess.TimeoutExpired(command, timeout)
    if not _PROCESS_SLOTS.acquire(blocking=False):
        raise ValueError("research_process_busy")
    process = None
    try:
        deadline = time.monotonic() + timeout
        with tempfile.TemporaryFile() as packet, tempfile.TemporaryFile() as errors, tempfile.TemporaryFile() as output:
            encoded = input.encode("utf-8")
            if len(encoded) > MAX_OUTPUT_BYTES:
                raise ValueError("research_packet_too_large")
            packet.write(encoded); packet.seek(0)
            process = (popen or subprocess.Popen)(
                command, cwd=cwd, stdin=packet, stdout=output if capture_stdout else subprocess.DEVNULL,
                stderr=errors, env=env, start_new_session=os.name == "posix",
            )
            while True:
                captured = errors.seek(0, os.SEEK_END) + output.seek(0, os.SEEK_END)
                if output_path is not None and Path(output_path).exists():
                    captured += Path(output_path).stat().st_size
                if captured > MAX_OUTPUT_BYTES:
                    raise ValueError("research_output_too_large")
                code = process.poll()
                if code is not None:
                    errors.seek(max(0, errors.tell() - 2048))
                    output.seek(0)
                    return subprocess.CompletedProcess(command, code, output.read(MAX_OUTPUT_BYTES).decode("utf-8", errors="replace"), errors.read(2048).decode("utf-8", errors="replace"))
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                time.sleep(min(.02, remaining))
    finally:
        try:
            if process is not None:
                _stop_process(process)
        finally:
            _PROCESS_SLOTS.release()


def bounded_external_call(operation, timeout):
    """不可取消的替身／來源 I/O 仍有固定總額度，逾時不釋放未結束工作額度。"""
    timeout = float(timeout)
    if not math.isfinite(timeout) or timeout <= 0 or not _IO_SLOTS.acquire(blocking=False):
        raise TimeoutError("research_io_busy_or_expired")
    mailbox = queue.Queue(maxsize=1)
    def invoke():
        try:
            try: mailbox.put((True, operation()))
            except BaseException as exc: mailbox.put((False, exc))
        finally:
            _IO_SLOTS.release()
    worker = threading.Thread(target=invoke, daemon=True, name="swing-external-io")
    try:
        worker.start()
    except BaseException:
        _IO_SLOTS.release()
        raise
    try:
        success, value = mailbox.get(timeout=timeout)
    except queue.Empty:
        raise TimeoutError("research_fixed_deadline") from None
    if not success: raise value
    return value


def validate_model_output(value, schema):
    """驗證目前隨附輸出 schema 使用的型別與約束，拒絕額外欄位／隱式型別轉換。"""
    kind = schema.get("type")
    matches = {
        "object": isinstance(value, dict), "array": isinstance(value, list),
        "string": isinstance(value, str), "boolean": type(value) is bool,
        "number": type(value) in (int, float) and math.isfinite(value),
    }
    if kind not in matches or not matches[kind]:
        raise ValueError("invalid_model_output_type")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("invalid_model_output_enum")
    if kind == "object":
        properties = schema.get("properties", {})
        if set(schema.get("required", [])) - value.keys() or (
            schema.get("additionalProperties") is False and value.keys() - properties.keys()
        ): raise ValueError("invalid_model_output_fields")
        for key, item in value.items():
            if key in properties: validate_model_output(item, properties[key])
    elif kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 10000):
            raise ValueError("invalid_model_output_items")
        for item in value: validate_model_output(item, schema["items"])
    elif kind == "number":
        if value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            raise ValueError("invalid_model_output_range")
    elif kind == "string" and "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
        raise ValueError("invalid_model_output_pattern")
