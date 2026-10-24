"""MCP server definition and transport wiring (targets the mcp 2.x SDK).

The tools here are thin adapters: they translate an MCP tool call into a call
into :mod:`executor`, which enforces the policy. Policy errors are returned to
the client as readable messages rather than raised, so the assistant can see
why a request was refused and adjust.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

from mcp.server.mcpserver import Image, MCPServer

from . import __version__, executor
from .allowlist import DynamicAllowlist
from .approvals import (
    Approver,
    AutoDenyApprover,
    FileApprovalQueue,
    TTYApprover,
)
from .config import ServerConfig
from .jobs import DEFAULT_JOB_TIMEOUT, Job, JobManager
from .patch import PatchSyntaxError
from .patch import apply_patch as apply_patch_impl
from .policy import Policy, PolicyError
from .sessions import SessionManager


def build_store(config: ServerConfig) -> DynamicAllowlist | None:
    """The persistent allowlist that 'always allow' appends to, if configured."""
    if config.allowlist_file:
        return DynamicAllowlist(config.allowlist_file)
    return None


def build_approver(config: ServerConfig) -> Approver:
    """Build the approver for commands not on the allowlist."""
    if config.approval_mode == "tty":
        return TTYApprover()
    if config.approval_mode == "file":
        assert config.approvals_dir is not None  # ensured by config.validate()
        return FileApprovalQueue(config.approvals_dir, config.approval_timeout)
    return AutoDenyApprover()


def build_server(config: ServerConfig) -> MCPServer:
    """Construct a configured :class:`MCPServer` with tools registered."""
    policy = config.policy
    parts = [
        "This server exposes one sandboxed local repo. Paths are confined to the "
        "project root, and commands run without a shell: one program per "
        "run_command — no pipes, redirects, chaining, or substitution. Search the "
        "tree with rg through run_command, and read long files in windows with "
        "read_file's offset/limit instead of whole-file reads. read_image returns "
        "an image you can actually see (plus a text line of its path and size)."
        "\n\n"
        "run_command is synchronous and bounded by this connector's request "
        "timeout, so for anything slow — installs, builds, test suites, asset "
        "extraction — use start_job instead: it returns a job id at once and the "
        "command keeps running on the machine. Follow it with get_job, see all "
        "jobs with list_jobs, block briefly with wait_job, and stop it with "
        "cancel_job. Jobs run in parallel; each has its own timeout (default 10 "
        "minutes, up to 12 hours)."
    ]
    if policy.allow_write:
        parts.append(
            "To change existing files, prefer apply_patch (an atomic V4A patch "
            "anchored by surrounding context) over rewriting a whole file with "
            "write_file. Read a file before patching so your context lines match; "
            "if a hunk fails to apply, re-read and rebuild it from fresh context."
        )
    if config.inbox_dir and policy.allow_write:
        parts.append(
            "For NEW art (sprites, icons, illustrations, textures): generate it "
            "with your own native image-generation tool, not by hand-writing or "
            "deriving SVG/pixel data in code. A rendered image is auto-saved into "
            "this project's import inbox; call list_inbox to see it, then "
            "import_recent_images or import_file to bring it into the repo."
        )
    mcp = MCPServer(
        "local-terminal-mcp",
        version=__version__,
        instructions="\n\n".join(parts),
    )

    store = build_store(config)
    approver = build_approver(config) if config.approval_mode != "none" else None
    sessions = SessionManager(policy.root)
    jobs_mgr = JobManager()

    allowed = ", ".join(sorted(str(p) for p in policy.allowed_commands))

    def _rel(path: Path) -> str:
        try:
            rel = path.relative_to(policy.root)
        except ValueError:
            return str(path)
        return str(rel) if str(rel) != "." else "."

    def _run(policy: Policy, command: str, base: Path) -> str:
        return executor.run_command(
            policy, command, cwd=str(base), store=store, approver=approver
        )

    @mcp.tool(
        description=(
            "Run a single allowlisted command and return its output. Exactly "
            "one program per call: pipes, chaining (&&, ;), redirects (>) and "
            "command substitution are not allowed. Use 'cd <dir>' to change the "
            "session's working directory (it persists across calls, so you need "
            "not repeat it); 'pwd' shows it. Pass 'session' to target a specific "
            "terminal opened with open_terminal (default: a shared session). "
            f"Allowed: {allowed}."
        )
    )
    def run_command(command: str, session: str = "", cwd: str = "") -> str:
        try:
            sess = sessions.get(session or None)
        except KeyError:
            return f"refused: unknown session {session!r} (open one first)"
        try:
            argv = policy.parse_structure(command)
        except PolicyError as exc:
            return f"refused: {exc}"

        program = os.path.basename(argv[0])
        if program == "cd":
            target = argv[1] if len(argv) > 1 else ""
            try:
                new_cwd = policy.resolve_within(sess.cwd, target)
            except PolicyError as exc:
                return f"refused: {exc}"
            if not new_cwd.is_dir():
                return f"refused: {target!r} is not a directory"
            sessions.set_cwd(sess.id, new_cwd)
            return f"cwd: {_rel(new_cwd)}"
        if program == "pwd":
            return _rel(sess.cwd)

        base = sess.cwd
        if cwd:
            try:
                base = policy.resolve_within(sess.cwd, cwd)
            except PolicyError as exc:
                return f"refused: {exc}"
        try:
            return _run(policy, command, base)
        except PolicyError as exc:
            return f"refused: {exc}"

    @mcp.tool(
        description=(
            "Open a new terminal session with its own persistent working "
            "directory (starts at the project root). Returns the session id to "
            "pass to run_command."
        )
    )
    def open_terminal(name: str = "") -> str:
        try:
            sess = sessions.open(name)
        except RuntimeError as exc:
            return f"refused: {exc}"
        return f"opened session '{sess.id}' (name: {sess.name}, cwd: {_rel(sess.cwd)})"

    @mcp.tool(description="List open terminal sessions and their directories.")
    def list_terminals() -> str:
        lines = [
            f"{s.id}  name={s.name}  cwd={_rel(s.cwd)}" for s in sessions.list()
        ]
        return "\n".join(lines) if lines else "(no sessions yet)"

    @mcp.tool(description="Close a terminal session by id.")
    def close_terminal(session: str) -> str:
        return (
            f"closed {session}"
            if sessions.close(session)
            else f"no such session {session!r}"
        )

    # -- background jobs ----------------------------------------------------
    # run_command is synchronous and bounded by the connector's own request
    # timeout. Anything slow (installs, builds, extractions) should run as a
    # background job: it returns a job id immediately and keeps running on this
    # machine while you poll, list, read, or cancel it. Jobs run in parallel.

    def _fmt_dur(seconds: float) -> str:
        s = int(seconds)
        if s < 60:
            return f"{s}s"
        if s < 3600:
            return f"{s // 60}m{s % 60:02d}s"
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"

    def _job_line(job: Job) -> str:
        rc = "" if job.returncode is None else f" exit={job.returncode}"
        cmd = job.command if len(job.command) <= 60 else job.command[:57] + "..."
        return (
            f"{job.id}  {job.status}{rc}  {_fmt_dur(job.elapsed())}  "
            f"cwd={_rel(Path(job.cwd))}  $ {cmd}"
        )

    def _job_detail(job: Job) -> str:
        text, shown, total = job.output_tail(policy.max_output_bytes)
        head = [
            f"job {job.id}  status={job.status}",
            f"  command: {job.command}",
            f"  cwd: {_rel(Path(job.cwd))}  pid: {job.pid}  "
            f"timeout: {job.timeout}s",
            f"  elapsed: {_fmt_dur(job.elapsed())}"
            + (
                f"  exit: {job.returncode}"
                if job.returncode is not None
                else " (running)"
            ),
        ]
        note = ""
        if total > shown:
            note = f"  [showing last {shown} of {total} bytes]"
        body = text if text else "(no output yet)"
        return "\n".join(head) + f"\n--- output{note} ---\n" + body

    @mcp.tool(
        description=(
            "Start a long-running command as a BACKGROUND job and return its id "
            "immediately, so a slow task (pip install, build, asset extraction, "
            "tests) never hits the connector's request timeout. Same rules as "
            "run_command: exactly one program, no pipes/chaining/redirects/"
            "substitution. The job keeps running on this machine; poll it with "
            "get_job, read every job with list_jobs, wait briefly with wait_job, "
            "and stop it with cancel_job. Jobs run in parallel. timeout_seconds "
            "defaults to 600 (10 min) and may be set up to 43200 (12 hours); the "
            "job is killed if it exceeds it. cwd is relative to the project root."
        )
    )
    def start_job(
        command: str, cwd: str = "", timeout_seconds: int = DEFAULT_JOB_TIMEOUT
    ) -> str:
        try:
            job = jobs_mgr.start(
                policy, command, cwd, timeout_seconds, store, approver
            )
        except PolicyError as exc:
            return f"refused: {exc}"
        return (
            f"started job {job.id} (pid {job.pid}, timeout {job.timeout}s). "
            f"Check it with get_job('{job.id}')."
        )

    @mcp.tool(
        description=(
            "List all background jobs (newest first) with status, exit code, "
            "elapsed time, working directory and command. Use get_job for the "
            "full output of one."
        )
    )
    def list_jobs() -> str:
        jobs = jobs_mgr.list()
        if not jobs:
            return "(no background jobs)"
        return "\n".join(_job_line(j) for j in jobs)

    @mcp.tool(
        description=(
            "Show a background job's current status and its captured output "
            "(combined stdout+stderr, most recent bytes). Call it repeatedly to "
            "follow progress; status is running until it becomes succeeded, "
            "failed, timed_out, cancelled, or error."
        )
    )
    def get_job(job_id: str) -> str:
        job = jobs_mgr.get(job_id)
        if job is None:
            return f"no such job {job_id!r} (use list_jobs)"
        return _job_detail(job)

    @mcp.tool(
        description=(
            "Wait up to wait_seconds for a background job to finish, then return "
            "its status and output — a convenient way to block briefly on a job "
            "you expect to complete soon instead of polling get_job in a loop. "
            "Returns while the job is still running if the wait elapses first."
        )
    )
    def wait_job(job_id: str, wait_seconds: int = 30) -> str:
        job = jobs_mgr.wait(job_id, max(0, min(int(wait_seconds), 300)))
        if job is None:
            return f"no such job {job_id!r} (use list_jobs)"
        return _job_detail(job)

    @mcp.tool(
        description=(
            "Cancel a running background job: its whole process group is sent "
            "SIGTERM, then SIGKILL if it does not exit promptly. Finished jobs "
            "are returned unchanged."
        )
    )
    def cancel_job(job_id: str) -> str:
        job = jobs_mgr.cancel(job_id)
        if job is None:
            return f"no such job {job_id!r} (use list_jobs)"
        if job.status == "cancelled":
            return f"cancelled job {job_id}"
        if not job.is_running:
            return f"job {job_id} already finished (status={job.status})"
        return f"stopping job {job_id} (status={job.status})"

    @mcp.tool(
        description=(
            "Read a UTF-8 text file inside the project root. For a long file, "
            "pass offset (1-based start line) and limit (number of lines) to read "
            "a window instead of the whole file; the result is prefixed with the "
            "line range, so you can tell there is more and page through it."
        )
    )
    def read_file(path: str, offset: int = 0, limit: int = 0) -> str:
        try:
            return executor.read_file(policy, path, offset, limit)
        except PolicyError as exc:
            return f"refused: {exc}"

    @mcp.tool(
        description="List the entries of a directory inside the project root."
    )
    def list_directory(path: str = ".") -> str:
        try:
            return executor.list_directory(policy, path)
        except PolicyError as exc:
            return f"refused: {exc}"

    @mcp.tool(
        description=(
            "List image files (and subfolders) in a directory inside the root, "
            "with sizes — image-filtered, unlike list_directory. Use it to find "
            "images to view with read_image or to share with image_url."
        )
    )
    def list_images(path: str = ".") -> str:
        try:
            listing = executor.list_images(policy, path)
        except PolicyError as exc:
            return f"refused: {exc}"
        lines = [f"{d}/" for d in listing["dirs"]]
        lines += [f"{img['path']}\t{img['bytes']}B" for img in listing["images"]]
        return "\n".join(lines) if lines else "(no images or subfolders here)"

    # A shareable image link only makes sense when the server is reachable at a
    # known public host (http transport with an allowed_hosts entry). Build the
    # base once; the tool is registered only when it can produce a real URL.
    public_base = None
    if config.transport == "http" and config.allowed_hosts:
        public_base = f"https://{config.allowed_hosts[0]}{config.mcp_path}"
    if public_base is not None:

        @mcp.tool(
            description=(
                "Return a shareable https link to a repo image that opens on any "
                "device over the tunnel — so you can hand the user a link without "
                "anyone navigating a gallery. Pass the repo-relative image path; "
                "set download=true for a save link instead of an inline view. The "
                "link carries the connector's secret path, so share it only with "
                "the user."
            )
        )
        def image_url(path: str, download: bool = False) -> str:
            try:
                target = executor.resolve_download(policy, path)
            except PolicyError as exc:
                return f"refused: {exc}"
            rel = str(target.relative_to(policy.root))
            url = f"{public_base}/download?path={quote(rel)}"
            return url + "&dl=1" if download else url

    @mcp.tool(
        description=(
            "Read an image file (png/jpg/gif/webp/bmp) inside the project root "
            "and return it as an image so you can actually see it. Large images "
            "are auto-downscaled to fit; the file on disk is left unchanged."
        ),
        structured_output=False,
    )
    def read_image(path: str):
        try:
            data, fmt = executor.read_image_payload(
                policy, path, executor.IMAGE_READ_MAX_BYTES
            )
        except PolicyError as exc:
            return f"refused: {exc}"
        # Large images are returned downscaled so the model can still see them;
        # the file on disk is unchanged. The text part keeps the turn useful if
        # the client drops the image block.
        text = executor.describe_image(policy, path, data, fmt)
        return [Image(data=data, format=fmt), text]

    if policy.allow_write:

        @mcp.tool(
            description=(
                "Write a UTF-8 text file inside the project root. Creates parent "
                "directories as needed. Only available when the server is started "
                "with write access enabled."
            )
        )
        def write_file(path: str, content: str) -> str:
            try:
                return executor.write_file(policy, path, content)
            except PolicyError as exc:
                return f"refused: {exc}"

        @mcp.tool(
            description=(
                "Write a BINARY file (e.g. a PNG) inside the project root from "
                "base64 content (a plain base64 string or a data: URL). Use this "
                "to save generated images and other non-text assets."
            )
        )
        def write_file_base64(path: str, data_base64: str) -> str:
            try:
                return executor.write_file_base64(policy, path, data_base64)
            except PolicyError as exc:
                return f"refused: {exc}"

        @mcp.tool(
            description=(
                "Apply a V4A patch that creates, updates, renames, and/or deletes "
                "one or more text files in the project root, atomically (every "
                "file changes or none do). Prefer this over rewriting a whole file "
                "with write_file when editing existing files.\n\n"
                "Read the file first, then anchor each change with ~3 unchanged "
                "context lines so it locates by content, not line numbers. "
                "Format:\n"
                "*** Begin Patch\n"
                "*** Update File: relative/path.py\n"
                "@@ optional enclosing-scope anchor\n"
                " unchanged context line\n"
                "-removed line\n"
                "+added line\n"
                "*** Add File: relative/new.py\n"
                "+first line of the new file\n"
                "*** Delete File: relative/old.py\n"
                "*** End Patch\n\n"
                "Rename by putting '*** Move to: relative/new/path.py' directly "
                "under an Update File header. Paths are relative to the root; "
                "absolute or '..' paths are refused. Text files only (use "
                "write_file_base64 for binary assets). If a hunk does not match, "
                "re-read the file and rebuild the hunk from fresh context."
            )
        )
        def apply_patch(input: str) -> str:
            try:
                return apply_patch_impl(policy, input)
            except PatchSyntaxError as exc:
                where = f" at line {exc.line}" if exc.line else ""
                return f"refused: patch syntax error{where}: {exc}"
            except PolicyError as exc:
                return f"refused: {exc}"

        @mcp.tool(
            description=(
                "Move or rename a file/directory within the project root "
                "(both source and destination are confined to the root)."
            )
        )
        def move_file(src: str, dest: str) -> str:
            try:
                return executor.move_file(policy, src, dest)
            except PolicyError as exc:
                return f"refused: {exc}"

        @mcp.tool(
            description=(
                "Copy a file or directory within the project root (both source "
                "and destination are confined to the root)."
            )
        )
        def copy_file(src: str, dest: str) -> str:
            try:
                return executor.copy_file(policy, src, dest)
            except PolicyError as exc:
                return f"refused: {exc}"

    if config.inbox_dir and policy.allow_write:
        inbox = Path(config.inbox_dir).expanduser()
        inbox.mkdir(parents=True, exist_ok=True)
        inbox_ttl = config.inbox_ttl_days

        def _clean() -> None:
            executor.cleanup_inbox(inbox, inbox_ttl)

        @mcp.tool(
            description=(
                "List recent image files in the import inbox (a staging folder), "
                "newest first. Use import_recent_images or import_file to bring "
                "them into the repo. Old files in the inbox are auto-cleaned."
            )
        )
        def list_inbox(images_only: bool = True, limit: int = 20) -> str:
            try:
                _clean()
                return executor.list_inbox(inbox, images_only, limit)
            except (PolicyError, OSError) as exc:
                return f"refused: {exc}"

        @mcp.tool(
            description=(
                "Import the most recent image(s) from the inbox into a folder "
                "inside the repo. Use this right after rendering new art with "
                "your own image-generation tool (it lands in the inbox "
                "automatically) or after the user downloads an image from "
                "ChatGPT. Set move=true to move instead of copy."
            )
        )
        def import_recent_images(
            dest_folder: str = "assets", count: int = 1, move: bool = False
        ) -> str:
            try:
                _clean()
                return executor.import_recent_images(
                    policy, inbox, dest_folder, count, move
                )
            except PolicyError as exc:
                return f"refused: {exc}"

        @mcp.tool(
            description=(
                "Import a specific file (by name) from the inbox into a path "
                "inside the repo. Set move=true to move instead of copy."
            )
        )
        def import_file(src_name: str, dest: str, move: bool = False) -> str:
            try:
                return executor.import_file(policy, inbox, src_name, dest, move)
            except PolicyError as exc:
                return f"refused: {exc}"

    if config.image_gen_cmd:

        @mcp.tool(
            description=(
                "Generate an image from a text prompt using the local image "
                "generator and save it to the given path inside the project root. "
                "Unlimited and local: the image is created on this machine. Then "
                "use read_image to view the result."
            )
        )
        def generate_image(prompt: str, output: str) -> str:
            try:
                return executor.generate_image(
                    policy,
                    prompt,
                    output,
                    config.image_gen_cmd,
                    config.image_timeout,
                )
            except PolicyError as exc:
                return f"refused: {exc}"

    return mcp


def _transport_security(config: ServerConfig):
    """Build DNS-rebinding settings that work with tunnels.

    DNS-rebinding protection validates the incoming ``Host`` header. Tunnels
    (ngrok, cloudflared) forward an arbitrary public hostname, which would be
    rejected by the default localhost-only allowlist. Since every HTTP request
    must already carry a valid bearer token, we default to accepting any host
    and let the user tighten it with ``--allowed-hosts`` for defense in depth.
    """
    from mcp.server.transport_security import TransportSecuritySettings

    if config.allowed_hosts:
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[*config.allowed_hosts, "127.0.0.1", "localhost"],
            allowed_origins=["*"],
        )
    return TransportSecuritySettings(enable_dns_rebinding_protection=False)


def run(config: ServerConfig) -> None:
    """Start the server using the configured transport."""
    config.validate()
    if config.inbox_dir and config.inbox_ttl_days > 0:
        executor.cleanup_inbox(Path(config.inbox_dir), config.inbox_ttl_days)
    mcp = build_server(config)

    if config.transport == "stdio":
        mcp.run(transport="stdio")
        return

    # HTTP transport: serve the Streamable-HTTP ASGI app with a health route
    # and the configured authentication, then serve with uvicorn.
    import html

    import uvicorn
    from starlette.responses import (
        FileResponse,
        HTMLResponse,
        JSONResponse,
        Response,
    )

    from .auth import HEALTH_PATH, BearerAuthMiddleware, health_endpoint

    policy = config.policy
    upload_max = 25_000_000
    allowed_origins = {"https://chatgpt.com", "https://chat.openai.com"}
    _image_media = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }

    def _cors(origin: str) -> dict[str, str]:
        if origin in allowed_origins:
            return {
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, X-Target-Path",
                "Access-Control-Max-Age": "600",
            }
        return {}

    async def upload_handler(request):
        """Receive raw image/file bytes from the browser extension and save them.

        The secret path segment is the credential; write mode and path
        containment limit what can be written. CORS is restricted to ChatGPT.
        """
        cors = _cors(request.headers.get("origin", ""))
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors)
        path = request.query_params.get("path") or request.headers.get(
            "x-target-path", ""
        )
        if not path:
            return JSONResponse(
                {"error": "missing 'path'"}, status_code=400, headers=cors
            )
        body = await request.body()
        try:
            target = executor.write_bytes(policy, path, body, upload_max)
        except PolicyError as exc:
            return JSONResponse(
                {"error": str(exc)}, status_code=403, headers=cors
            )
        return JSONResponse(
            {"ok": True, "path": str(target), "bytes": len(body)}, headers=cors
        )

    async def images_handler(request):
        """List images (and subfolders) in a directory inside the root, as JSON."""
        rel = request.query_params.get("path", ".")
        try:
            listing = executor.list_images(policy, rel)
        except PolicyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        return JSONResponse(listing)

    async def download_handler(request):
        """Stream an image file from inside the root. ``?dl=1`` forces a save."""
        path = request.query_params.get("path", "")
        if not path:
            return JSONResponse({"error": "missing 'path'"}, status_code=400)
        try:
            target = executor.resolve_download(policy, path)
        except PolicyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        media = _image_media.get(target.suffix.lower(), "application/octet-stream")
        disposition = "attachment" if request.query_params.get("dl") else "inline"
        return FileResponse(
            target,
            media_type=media,
            filename=target.name,
            content_disposition_type=disposition,
        )

    def _dl_url(rel_path: str, *, download: bool = False) -> str:
        url = f"{config.mcp_path}/download?path={quote(rel_path)}"
        return url + "&dl=1" if download else url

    async def gallery_handler(request):
        """A minimal browsable image gallery for other devices (same-origin)."""
        rel = request.query_params.get("path", ".")
        try:
            listing = executor.list_images(policy, rel)
        except PolicyError as exc:
            return HTMLResponse(
                f"<p>{html.escape(str(exc))}</p>", status_code=403
            )
        folders = "".join(
            f'<li><a href="{config.mcp_path}/gallery?path={quote(d)}">'
            f"\N{FILE FOLDER} {html.escape(d)}</a></li>"
            for d in listing["dirs"]
        )
        tiles = "".join(
            f'<figure><a href="{_dl_url(img["path"])}" target="_blank">'
            f'<img loading="lazy" src="{_dl_url(img["path"])}" '
            f'alt="{html.escape(img["name"])}"></a>'
            f'<figcaption>{html.escape(img["name"])} '
            f'<a href="{_dl_url(img["path"], download=True)}">\N{DOWNWARDS ARROW}</a>'
            f"</figcaption></figure>"
            for img in listing["images"]
        )
        here = html.escape(listing["dir"])
        page = (
            "<!doctype html><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>images: {here}</title><style>"
            "body{font-family:system-ui,sans-serif;margin:0;padding:16px;"
            "background:#111;color:#eee}h1{font-size:15px;font-weight:600;"
            "word-break:break-all}ul{list-style:none;padding:0}"
            "li a{color:#6cf;text-decoration:none;line-height:1.9}"
            ".grid{display:grid;gap:12px;grid-template-columns:"
            "repeat(auto-fill,minmax(150px,1fr))}figure{margin:0}"
            "img{width:100%;height:150px;object-fit:cover;border-radius:8px;"
            "background:#222;display:block}figcaption{font-size:12px;"
            "color:#aaa;margin-top:4px;word-break:break-all}"
            "figcaption a{color:#6cf;text-decoration:none}</style>"
            f"<h1>images: {here}</h1><ul>{folders}</ul>"
            f"<div class=grid>{tiles}</div>"
        )
        return HTMLResponse(page)

    # Register an unauthenticated health route on the MCP app.
    mcp.custom_route(HEALTH_PATH, methods=["GET"])(health_endpoint)
    # These all live under the secret path, so the secret gates them. Upload
    # takes raw bytes (browser extension / curl / Shortcut); the read routes let
    # any device browse and download repo images over the tunnel.
    mcp.custom_route(config.mcp_path + "/upload", methods=["POST", "OPTIONS"])(
        upload_handler
    )
    mcp.custom_route(config.mcp_path + "/images", methods=["GET"])(images_handler)
    mcp.custom_route(config.mcp_path + "/download", methods=["GET"])(
        download_handler
    )
    mcp.custom_route(config.mcp_path + "/gallery", methods=["GET"])(gallery_handler)

    app = mcp.streamable_http_app(
        host=config.host,
        streamable_http_path=config.mcp_path,
        transport_security=_transport_security(config),
    )

    if config.auth_mode == "bearer":
        # Guaranteed by config.validate().
        assert config.auth_token is not None
        app.add_middleware(BearerAuthMiddleware, token=config.auth_token)
    # In "path" mode the unguessable mcp_path is the credential: the MCP route
    # only exists at that path, so unauthenticated probes of other paths 404.

    uvicorn.run(app, host=config.host, port=config.port, log_level="info")
