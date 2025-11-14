import argparse
import html
import json
import subprocess
import sys
from io import BytesIO
from pathlib import Path


class CommandResult:
    """Minimal subprocess result representation compatible with Python 3.6."""

    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def run_command(command):
    """Execute a subprocess and return decoded stdout/stderr text."""

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout_data, stderr_data = process.communicate()
    stdout_text = (
        stdout_data.decode("utf-8", "replace")
        if isinstance(stdout_data, bytes)
        else stdout_data
    )
    stderr_text = (
        stderr_data.decode("utf-8", "replace")
        if isinstance(stderr_data, bytes)
        else stderr_data
    )
    return CommandResult(process.returncode, stdout_text, stderr_text)


def _escape_pdf_text(value: str) -> str:
    """Escape text for inclusion in a PDF string."""
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _write_pdf(lines, output_path):
    """Write lines of text to a simple PDF file without extra dependencies."""
    lines = lines or [""]
    content_parts = ["BT", "/F1 12 Tf", "14 TL", "72 720 Td"]
    for index, line in enumerate(lines):
        if index:
            content_parts.append("T*")
        content_parts.append(f"({_escape_pdf_text(line)}) Tj")
    content_parts.append("ET")
    content_bytes = "\n".join(content_parts).encode("latin-1", "replace")

    buffer = BytesIO()
    buffer.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []

    def add_object(obj_id, body_bytes):
        offsets.append(buffer.tell())
        buffer.write(f"{obj_id} 0 obj\n".encode("latin-1"))
        buffer.write(body_bytes)
        buffer.write(b"\nendobj\n")

    add_object(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    add_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    add_object(
        3,
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 5 0 R "
        b"/Resources << /Font << /F1 4 0 R >> >> >>",
    )
    add_object(4, b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")
    add_object(
        5,
        b"<< /Length "
        + str(len(content_bytes)).encode("latin-1")
        + b" >>\nstream\n"
        + content_bytes
        + b"\nendstream",
    )

    xref_offset = buffer.tell()
    buffer.write(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets:
        buffer.write(f"{offset:010d} 00000 n \n".encode("latin-1"))
    buffer.write(b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n")
    buffer.write(str(xref_offset).encode("latin-1"))
    buffer.write(b"\n%%EOF")

    Path(output_path).write_bytes(buffer.getvalue())


def _escape_rtf(value: str) -> str:
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _write_doc(lines, output_path):
    """Write lines of text to a simple RTF document with a .doc extension."""
    lines = lines or [""]
    rtf_lines = ["{\\rtf1\\ansi"]
    for line in lines:
        rtf_lines.append(_escape_rtf(line) + "\\line")
    rtf_lines.append("}")
    Path(output_path).write_text("\n".join(rtf_lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description="Run middleware_healthcheck.py and output results in various formats"
    )
    parser.add_argument(
        "--format",
        choices=["json", "html", "pdf", "doc"],
        required=True,
        help="Output format",
    )
    parser.add_argument("--output", required=True, help="Output file path")
    parser.add_argument(
        "healthcheck_args",
        nargs=argparse.REMAINDER,
        help="Arguments to pass through to middleware_healthcheck.py",
    )
    args = parser.parse_args()

    extra_args = args.healthcheck_args
    if extra_args and extra_args[0] == "--":
        extra_args = extra_args[1:]
    cmd = [sys.executable, "middleware_healthcheck.py"] + extra_args
    result = run_command(cmd)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(result.returncode)

    output = result.stdout

    lines = output.splitlines()

    if args.format == "json":
        data = {"output": lines}
        with open(args.output, "w") as f:
            json.dump(data, f, indent=2)
        return

    if args.format == "html":
        html_content = "<html><body><pre>" + html.escape(output) + "</pre></body></html>"
        with open(args.output, "w") as f:
            f.write(html_content)
        return

    if args.format == "pdf":
        _write_pdf(lines, args.output)
        return

    if args.format == "doc":
        _write_doc(lines, args.output)
        return


if __name__ == "__main__":
    main()
