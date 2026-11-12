"""Contain one owned worker using the previously validated process-tree pattern."""
import os
import signal
import subprocess


def execute_worker(command, output, environment, timeout):
    with (output / "stdout.log").open("w", encoding="utf-8") as stdout, (output / "stderr.log").open("w", encoding="utf-8") as stderr:
        child = subprocess.Popen(command, cwd=output, env=environment, stdout=stdout, stderr=stderr,
                                 stdin=subprocess.DEVNULL, start_new_session=os.name == "posix")
        result = {"worker_pid": child.pid, "timeout": False}
        try:
            result["exit_code"] = child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            result["timeout"] = True
            if child.poll() is None:
                if os.name == "nt":
                    cleanup = subprocess.run(["taskkill.exe", "/PID", str(child.pid), "/T", "/F"],
                                             text=True, capture_output=True, timeout=15)
                    result["tree_cleanup_exit_code"] = cleanup.returncode
                    result["tree_cleanup_stdout"] = cleanup.stdout
                    result["tree_cleanup_stderr"] = cleanup.stderr
                else:
                    try:
                        os.killpg(child.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            try:
                result["exit_code"] = child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    os.killpg(child.pid, signal.SIGKILL)
                else:
                    child.kill()
                result["exit_code"] = child.wait(timeout=10)
        return result
