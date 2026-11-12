/* Sends files bigger than ~3 MB in pieces (hosting limits a single request to 4.5 MB), then submits the form. */
(function () {
  const LIMIT = 3 * 1024 * 1024, PIECE = 3 * 1024 * 1024;
  document.addEventListener("submit", async (e) => {
    const form = e.target;
    const inp = form.querySelector('input[type=file][name="file"]');
    if (!inp || !inp.files.length || inp.files[0].size <= LIMIT || form.dataset.chunked) return;
    e.preventDefault();
    const f = inp.files[0], csrf = (form.querySelector("[name=csrfmiddlewaretoken]") || {}).value || "";
    const token = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random()).replace(/-/g, "").slice(0, 32);
    const btn = form.querySelector("button[type=submit], button:not([type])");
    const note = document.createElement("div"); note.className = "small muted"; form.appendChild(note);
    if (btn) btn.disabled = true;
    try {
      const n = Math.ceil(f.size / PIECE);
      for (let i = 0; i < n; i++) {
        note.textContent = `Uploading ${f.name}: part ${i + 1} of ${n}…`;
        const r = await fetch("/api/chunk/", { method: "POST", body: f.slice(i * PIECE, (i + 1) * PIECE),
          headers: { "X-CSRFToken": csrf, "X-Chunk-Token": token, "X-Chunk-Index": String(i), "Content-Type": "application/octet-stream" } });
        if (!r.ok) throw new Error(`part ${i + 1} failed (${r.status})`);
      }
      note.textContent = `Uploaded — processing ${f.name}…`;
      const add = (k, v) => { const h = document.createElement("input"); h.type = "hidden"; h.name = k; h.value = v; form.appendChild(h); };
      add("chunk_token", token); add("chunk_name", f.name);
      inp.value = ""; inp.removeAttribute("required"); inp.disabled = true;
      // keep the multipart encoding (an empty file field) so the server reads the form the same way
      const dummy = document.createElement("input"); dummy.type = "file"; dummy.name = "_none"; dummy.style.display = "none"; form.appendChild(dummy);
      form.dataset.chunked = "1";
      form.submit();
    } catch (err) {
      note.textContent = "Upload failed: " + err.message;
      if (btn) btn.disabled = false;
    }
  }, true);
})();
