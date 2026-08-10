#!/usr/bin/env python3
"""
Thickness License Studio — desktop app for OUR team to generate the activation
details we hand to each customer.

This is the no-terminal counterpart of tools/local_license_tool.py: instead of
typing a CLI command, a team member fills in a form (company, machine code,
mode, term) and clicks Generate. The app signs the license with our Ed25519
private key and shows the printable activation card (license code + dashboard
login) ready to copy, save, or print.

It runs on OUR machine only — it needs the private signing key directory
(default %USERPROFILE%\\thickness-license-keys). It never talks to any server;
the appliance verifies the code offline with the bundled public key.

Run from source:   python tools/license_app.py
Build to an .exe:  see tools/build-license-app.txt
"""
import os
import sys
import tempfile
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import local_license_tool as lt


class LicenseApp(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=12)
        self.pack(fill="both", expand=True)
        self.last = None  # last issue() result

        self.keydir = tk.StringVar(value=lt.default_keydir())
        self.company = tk.StringVar()
        self.mode = tk.StringVar(value="opposite")
        self.machine = tk.StringVar()
        self.any_machine = tk.BooleanVar(value=False)
        self.term = tk.StringVar(value="perpetual")  # perpetual | days
        self.days = tk.StringVar(value="365")
        self.admin_user = tk.StringVar(value="admin")
        self.admin_pw = tk.StringVar()
        self.note = tk.StringVar()

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        nb.add(self._build_generate_tab(nb), text="  Generate Card  ")
        nb.add(self._build_registry_tab(nb), text="  Registry  ")

    # ---- Generate tab -------------------------------------------------------
    def _build_generate_tab(self, parent):
        f = ttk.Frame(parent, padding=12)
        f.columnconfigure(1, weight=1)
        r = 0

        def row(label, widget, hint=None):
            nonlocal r
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=4, padx=(0, 10))
            widget.grid(row=r, column=1, sticky="ew", pady=4)
            if hint:
                r += 1
                ttk.Label(f, text=hint, foreground="#64748b", font=("", 8)).grid(
                    row=r, column=1, sticky="w")
            r += 1

        # Key directory
        keyrow = ttk.Frame(f)
        keyrow.columnconfigure(0, weight=1)
        ttk.Entry(keyrow, textvariable=self.keydir).grid(row=0, column=0, sticky="ew")
        ttk.Button(keyrow, text="Browse…", command=self._browse_keydir).grid(row=0, column=1, padx=(6, 0))
        row("Signing key folder", keyrow, "Holds our private key + registry — never share this.")

        row("Company name", ttk.Entry(f, textvariable=self.company),
            "Also becomes the customer's dashboard login company.")

        row("Sensor mode", ttk.Combobox(f, textvariable=self.mode, state="readonly",
                                         values=["opposite", "sbs"], width=18))

        # Machine code + any-machine
        mrow = ttk.Frame(f)
        mrow.columnconfigure(0, weight=1)
        self._machine_entry = ttk.Entry(mrow, textvariable=self.machine)
        self._machine_entry.grid(row=0, column=0, sticky="ew")
        ttk.Checkbutton(mrow, text="Any machine (loaner/spare)", variable=self.any_machine,
                        command=self._toggle_machine).grid(row=0, column=1, padx=(6, 0))
        row("Machine code", mrow, "The XXXX-XXXX-XXXX-XXXX shown on the appliance's activation page.")

        # Term
        trow = ttk.Frame(f)
        ttk.Radiobutton(trow, text="Perpetual", value="perpetual", variable=self.term,
                        command=self._toggle_term).grid(row=0, column=0)
        ttk.Radiobutton(trow, text="Term (days):", value="days", variable=self.term,
                        command=self._toggle_term).grid(row=0, column=1, padx=(12, 4))
        self._days_entry = ttk.Entry(trow, textvariable=self.days, width=8, state="disabled")
        self._days_entry.grid(row=0, column=2)
        row("License term", trow, "Perpetual = sold outright. Term = subscription; re-issue to renew.")

        row("Admin username", ttk.Entry(f, textvariable=self.admin_user))
        row("Admin password", ttk.Entry(f, textvariable=self.admin_pw),
            "Leave blank to auto-generate a strong password.")
        row("Note (optional)", ttk.Entry(f, textvariable=self.note),
            "Free text stored in the registry — e.g. PO number.")

        ttk.Button(f, text="Generate activation card", command=self._on_generate).grid(
            row=r, column=0, columnspan=2, sticky="ew", pady=(10, 6))
        r += 1

        # Output
        self.out = tk.Text(f, height=16, width=66, wrap="none", font=("Consolas", 9))
        self.out.grid(row=r, column=0, columnspan=2, sticky="nsew")
        f.rowconfigure(r, weight=1)
        r += 1

        btns = ttk.Frame(f)
        btns.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        for i, (txt, cmd) in enumerate([
            ("Copy card", self._copy_card),
            ("Copy license code", self._copy_code),
            ("Save card…", self._save_card),
            ("Print", self._print_card),
        ]):
            ttk.Button(btns, text=txt, command=cmd).grid(row=0, column=i, padx=(0, 6))
        return f

    # ---- Registry tab -------------------------------------------------------
    def _build_registry_tab(self, parent):
        f = ttk.Frame(parent, padding=12)
        cols = ("license_id", "customer", "sensor_mode", "machine_code",
                "issued_at", "expires_at", "note")
        heads = ("License", "Customer", "Mode", "Machine", "Issued", "Expires", "Note")
        self.tree = ttk.Treeview(f, columns=cols, show="headings", height=16)
        for c, h in zip(cols, heads):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=110 if c != "customer" else 160, anchor="w")
        self.tree.pack(fill="both", expand=True)
        ttk.Button(f, text="Refresh", command=self._refresh_registry).pack(anchor="e", pady=(8, 0))
        return f

    # ---- actions ------------------------------------------------------------
    def _browse_keydir(self):
        d = filedialog.askdirectory(initialdir=self.keydir.get() or os.path.expanduser("~"))
        if d:
            self.keydir.set(d)

    def _toggle_machine(self):
        self._machine_entry.config(state="disabled" if self.any_machine.get() else "normal")

    def _toggle_term(self):
        self._days_entry.config(state="normal" if self.term.get() == "days" else "disabled")

    def _on_generate(self):
        try:
            days = int(self.days.get()) if self.term.get() == "days" else None
        except ValueError:
            messagebox.showerror("Invalid term", "Days must be a whole number.")
            return
        try:
            res = lt.issue(
                self.keydir.get().strip(),
                self.company.get(),
                mode=self.mode.get(),
                machine=self.machine.get(),
                any_machine=self.any_machine.get(),
                days=days,
                admin_username=self.admin_user.get().strip() or "admin",
                admin_password=self.admin_pw.get().strip() or None,
                note=self.note.get().strip(),
            )
        except SystemExit as e:  # load_private_key uses sys.exit on missing key
            messagebox.showerror("Signing key not found", str(e))
            return
        except ValueError as e:
            messagebox.showwarning("Check the form", str(e))
            return
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Could not generate", repr(e))
            return

        self.last = res
        self.out.delete("1.0", "end")
        self.out.insert("1.0", res["card"])
        self._refresh_registry()
        messagebox.showinfo("Card generated",
                            f"License {res['payload']['license_id']} for "
                            f"{res['payload']['customer']} issued.\n"
                            f"{res['count']} license(s) in the registry.")

    def _copy_card(self):
        if self._require(): self._to_clip(self.last["card"])

    def _copy_code(self):
        if self._require(): self._to_clip(self.last["code"])

    def _save_card(self):
        if not self._require():
            return
        slug = "".join(c if c.isalnum() else "_" for c in self.last["payload"]["customer"].lower())
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            initialfile=f"activation_card_{slug}.txt",
            filetypes=[("Text file", "*.txt")])
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.last["card"])
            messagebox.showinfo("Saved", f"Activation card saved to:\n{path}")

    def _print_card(self):
        if not self._require():
            return
        try:
            fd, path = tempfile.mkstemp(suffix=".txt", prefix="activation_card_")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(self.last["card"])
            if sys.platform.startswith("win"):
                os.startfile(path, "print")  # noqa: S606 — trusted local temp file
            else:
                messagebox.showinfo("Print", f"Saved for printing:\n{path}")
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Print failed", repr(e))

    def _refresh_registry(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for e in lt.load_registry(self.keydir.get().strip()):
            self.tree.insert("", "end", values=(
                e.get("license_id", ""), e.get("customer", ""), e.get("sensor_mode", ""),
                e.get("machine_code", ""), e.get("issued_at", ""),
                e.get("expires_at") or "never", e.get("note", "")))

    def _require(self):
        if not self.last:
            messagebox.showinfo("Nothing yet", "Generate a card first.")
            return False
        return True

    def _to_clip(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()


def main():
    root = tk.Tk()
    root.title("Thickness License Studio")
    root.geometry("640x760")
    try:
        root.call("tk", "scaling", 1.2)
    except tk.TclError:
        pass
    LicenseApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
