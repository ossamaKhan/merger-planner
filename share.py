"""
Share the planner with someone anywhere — no terminal, no hosting.

HOW TO USE
  Double-click share.py   (or open it in VS Code / PyCharm / Spyder / IDLE and press Run)

  A small window opens straight away and shows every step. The first run can take
  a few minutes (it installs packages and downloads the tunnel program).
  When it is ready the window shows a public https link + a login, with Copy buttons.
  Everything is also written to share_log.txt next to this file.

  Keep the window open while your tester uses the link — closing it stops sharing.
"""
import os
import queue
import secrets
import socket
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable
if PY.lower().endswith("pythonw.exe"):  # double-clicked .pyw: use console python for child processes
    PY = PY[:-5] + ".exe"
INFO_FILE = HERE / "share_link.txt"
LOG_FILE = HERE / "share_log.txt"
TESTER = "tester"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # don't pop extra black windows on Windows

events = queue.Queue()  # (kind, payload) -> GUI
state = {"server": None, "tunnel": None}


# ----------------------------------------------------------------------------- output
def say(msg=""):
    line = str(msg)
    try:
        print(line, flush=True)
    except Exception:  # noqa: BLE001  (no console when started with pythonw)
        pass
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    events.put(("log", line))


def step(n, text):
    say(f"\n[{n}/5] {text}")
    events.put(("step", (n, text)))


def run_logged(cmd, check=True):
    """Run a command, streaming its output into the window/log."""
    p = subprocess.Popen(cmd, cwd=HERE, env=os.environ, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    for line in p.stdout:
        line = line.rstrip()
        if line:
            say("      " + line)
    p.wait()
    if check and p.returncode != 0:
        raise RuntimeError(f"Command failed ({p.returncode}): {' '.join(map(str, cmd[1:4]))} …")
    return p.returncode


# ----------------------------------------------------------------------------- steps
def ensure_packages():
    step(1, "Checking packages (first run may install some — can take a few minutes)")
    need = []
    for mod, pkg in (("django", "Django"), ("pandas", "pandas"), ("openpyxl", "openpyxl"), ("shapely", "shapely"),
                     ("numpy", "numpy"), ("whitenoise", "whitenoise"), ("pycloudflared", "pycloudflared")):
        try:
            __import__(mod)
        except ImportError:
            need.append(pkg)
    if not need:
        say("      all packages present")
        return
    say("      installing: " + ", ".join(need))
    for extra in ([], ["--user"]):
        if run_logged([PY, "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off", *extra, *need], check=False) == 0:
            import importlib
            importlib.invalidate_caches()
            return
    raise RuntimeError("Could not install " + ", ".join(need) + ".\n"
                       "If pip is blocked on this laptop, ask IT to install these packages for your Python.")


def free_port():
    for port in [8000] + list(range(8001, 8050)):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("No free port between 8000 and 8049.")


def ensure_tester():
    """Create/refresh the 'tester' staff login (the app's login page only accepts staff accounts)."""
    sys.path.insert(0, str(HERE))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    from django.contrib.auth import get_user_model

    User = get_user_model()
    password = None
    if INFO_FILE.exists():
        for line in INFO_FILE.read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("password:"):
                password = line.split(":", 1)[1].strip()
    user = User.objects.filter(username=TESTER).first()
    if user is None or not password or not user.check_password(password):
        password = secrets.token_urlsafe(9)
        user = user or User(username=TESTER)
        user.is_staff = True  # needed to log in; it has no admin permissions
        user.is_active = True
        user.set_password(password)
        user.save()
    INFO_FILE.write_text(f"Username: {TESTER}\nPassword: {password}\n", encoding="utf-8")
    return password


def work():
    try:
        os.chdir(HERE)
        LOG_FILE.write_text(f"share.py started {time.strftime('%Y-%m-%d %H:%M:%S')}  python={sys.executable}\n", encoding="utf-8")
        if not (HERE / "manage.py").exists():
            raise RuntimeError(f"share.py must sit in the project folder next to manage.py.\nIt is in: {HERE}")
        ensure_packages()

        step(2, "Securing settings for outside access (login on, debug pages off)")
        os.environ["PLANNER_REQUIRE_LOGIN"] = "1"
        os.environ["DJANGO_DEBUG"] = "0"
        os.environ["DJANGO_ALLOWED_HOSTS"] = "*"
        port = free_port()
        os.environ["DJANGO_CSRF_ORIGINS"] = f"https://*.trycloudflare.com,http://127.0.0.1:{port},http://localhost:{port}"

        step(3, "Preparing database, files and the tester login")
        run_logged([PY, "manage.py", "migrate", "--noinput", "-v", "1"])
        run_logged([PY, "manage.py", "collectstatic", "--noinput", "-v", "0"], check=False)
        password = ensure_tester()
        say(f"      login: {TESTER} / {password}")
        events.put(("login", (TESTER, password)))

        step(4, f"Starting the planner on this laptop (port {port})")
        server = subprocess.Popen([PY, "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload", "--insecure"],
                                  cwd=HERE, env=os.environ, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        state["server"] = server
        threading.Thread(target=lambda: [say("      " + ln.rstrip()) for ln in server.stdout if ln.strip()], daemon=True).start()
        for _ in range(120):
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            if server.poll() is not None:
                raise RuntimeError("The planner stopped while starting — see the messages above.")
            time.sleep(0.5)
        local = f"http://127.0.0.1:{port}"
        say(f"      running at {local}")
        events.put(("local", local))

        step(5, "Opening the public link (first run downloads the Cloudflare tunnel, ~40 MB)")
        from pycloudflared import try_cloudflare

        try:
            urls = try_cloudflare(port=port, verbose=False)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Could not open the public link: {exc}\n\n"
                "The office network probably blocks it. Connect the laptop to a mobile hotspot and run share.py again.\n"
                f"The planner still works on this laptop at {local}"
            )
        state["tunnel"] = urls.process
        url = urls.tunnel
        INFO_FILE.write_text(f"Link: {url}\nUsername: {TESTER}\nPassword: {password}\n"
                             f"Started: {time.strftime('%Y-%m-%d %H:%M')}\n(The link changes every time share.py is run.)\n",
                             encoding="utf-8")
        say("\n" + "#" * 60 + f"\n  LINK:      {url}\n  USERNAME:  {TESTER}\n  PASSWORD:  {password}\n"
            "  Keep this window open while your tester uses it.\n" + "#" * 60)
        events.put(("ready", url))
    except Exception as exc:  # noqa: BLE001
        say("\nERROR: " + str(exc))
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        events.put(("error", str(exc)))


def shutdown():
    for key in ("tunnel", "server"):
        p = state.get(key)
        if p and p.poll() is None:
            try:
                p.terminate()
            except Exception:  # noqa: BLE001
                pass


# ----------------------------------------------------------------------------- window
def run_window():
    import tkinter as tk
    from tkinter import scrolledtext

    root = tk.Tk()
    root.title("Franchise Planner — share link")
    root.geometry("760x560")
    root.configure(bg="#f4f5f8")
    try:
        root.attributes("-topmost", True)
        root.after(1500, lambda: root.attributes("-topmost", False))
    except tk.TclError:
        pass
    navy, orange = "#1f2a44", "#ff7a00"

    head = tk.Frame(root, bg=navy)
    head.pack(fill="x")
    tk.Label(head, text="Franchise Planner · share a temporary public link", fg="white", bg=navy,
             font=("Segoe UI", 13, "bold"), pady=10, padx=14).pack(anchor="w")

    status = tk.Label(root, text="Starting…", bg="#f4f5f8", fg=navy, font=("Segoe UI", 11, "bold"), anchor="w", padx=14, pady=8)
    status.pack(fill="x")

    card = tk.Frame(root, bg="white", highlightbackground="#e3e6ed", highlightthickness=1, padx=14, pady=10)
    card.pack(fill="x", padx=14)
    fields = {}

    def row(label, key):
        fr = tk.Frame(card, bg="white")
        fr.pack(fill="x", pady=2)
        tk.Label(fr, text=label, width=10, anchor="w", bg="white", fg="#7d8597", font=("Segoe UI", 10)).pack(side="left")
        var = tk.StringVar(value="…")
        ent = tk.Entry(fr, textvariable=var, font=("Consolas", 11, "bold"), relief="flat", readonlybackground="white", state="readonly")
        ent.pack(side="left", fill="x", expand=True)

        def copy():
            root.clipboard_clear()
            root.clipboard_append(var.get())
            status.config(text=f"Copied {label.lower()} to clipboard")

        tk.Button(fr, text="Copy", command=copy, relief="groove").pack(side="left", padx=4)
        fields[key] = var
        return fr

    link_row = row("Link", "link")
    row("Username", "user")
    row("Password", "pass")
    btns = tk.Frame(card, bg="white")
    btns.pack(fill="x", pady=(6, 0))
    local_url = {"v": ""}
    open_btn = tk.Button(btns, text="Open link in browser", state="disabled", bg=orange, fg="white", relief="flat", padx=10,
                         disabledforeground="#ffd9b3",
                         command=lambda: webbrowser.open(fields["link"].get() if fields["link"].get().startswith("https") else local_url["v"]))
    open_btn.pack(side="left")
    copy_all = tk.Button(btns, text="Copy all (to send)", state="disabled", relief="groove", padx=10)
    copy_all.pack(side="left", padx=6)

    def do_copy_all():
        root.clipboard_clear()
        root.clipboard_append(f"Franchise Planner\nLink: {fields['link'].get()}\nUsername: {fields['user'].get()}\nPassword: {fields['pass'].get()}")
        status.config(text="Copied link + login — paste it into WhatsApp / email")

    copy_all.config(command=do_copy_all)

    tk.Label(root, text="Progress (also saved in share_log.txt)", bg="#f4f5f8", fg="#7d8597", anchor="w", padx=14, pady=4).pack(fill="x")
    log = scrolledtext.ScrolledText(root, height=14, font=("Consolas", 9), bg="#111827", fg="#d1d5db", relief="flat")
    log.pack(fill="both", expand=True, padx=14, pady=(0, 6))
    tk.Label(root, text="Keep this window open while your tester uses the link. Closing it stops sharing.",
             bg="#f4f5f8", fg="#b45309", anchor="w", padx=14, pady=6).pack(fill="x")

    def poll():
        try:
            while True:
                kind, val = events.get_nowait()
                if kind == "log":
                    log.insert("end", val + "\n")
                    log.see("end")
                elif kind == "step":
                    status.config(text=f"Step {val[0]} of 5 — {val[1]}", fg=navy)
                elif kind == "login":
                    fields["user"].set(val[0])
                    fields["pass"].set(val[1])
                elif kind == "local":
                    fields["link"].set(val + "   (this laptop only — public link coming…)")
                    local_url["v"] = val
                    open_btn.config(state="normal", text="Open on this laptop")
                elif kind == "ready":
                    fields["link"].set(val)
                    open_btn.config(text="Open link in browser")
                    status.config(text="READY — send the link and login to your tester", fg="#13824a")
                    open_btn.config(state="normal")
                    copy_all.config(state="normal")
                    link_row.configure(bg="#e5f6ec")
                elif kind == "error":
                    status.config(text="Problem — see the message below (also in share_log.txt)", fg="#c81e1e")
        except queue.Empty:
            pass
        root.after(200, poll)

    def on_close():
        shutdown()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    threading.Thread(target=work, daemon=True).start()
    poll()
    root.mainloop()
    shutdown()


def run_console():
    say("(no window available — showing progress here)")
    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join()
    ok = False
    while not events.empty():
        kind, _ = events.get()
        ok = ok or kind == "ready"
    srv = state.get("server")
    try:
        if ok and srv:
            srv.wait()
        else:
            input("\nPress Enter to close…")
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        shutdown()


if __name__ == "__main__":
    try:
        import tkinter  # noqa: F401

        try:
            run_window()
        except tkinter.TclError:
            run_console()
    except ImportError:
        run_console()
