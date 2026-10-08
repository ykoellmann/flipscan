let S = null, sel = 0;
const COLORS = ["#4fc3f7","#aed581","#ffb74d","#ba68c8","#f06292","#4db6ac","#fff176"];
const $ = s => document.querySelector(s);

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {method: "POST", body: JSON.stringify(body)});
  return r.json();
}
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.hidden = false; setTimeout(() => t.hidden = true, 4000); }

function render() {
  const docOf = {};
  S.documents.forEach((d, i) => d.forEach(id => docOf[id] = i));
  // sheet grouping, in scan order
  const sheets = [];
  S.pages.forEach(p => {
    const last = sheets[sheets.length - 1];
    if (last && last.id === p.sheet) last.pages.push(p); else sheets.push({id: p.sheet, pages: [p]});
  });
  const strip = $("#strip"); strip.innerHTML = "";
  let curDoc = 0;
  sheets.forEach(sh => {
    const el = document.createElement("div");
    const first = sh.pages[0];
    const d = sh.pages.map(p => docOf[p.id]).find(x => x !== undefined);
    if (d !== undefined) curDoc = d;
    el.className = "sheet" + (sh.pages.some(p => p.cut_before) ? " cut" : "");
    el.style.setProperty("--c", COLORS[curDoc % COLORS.length]);
    sh.pages.forEach(p => {
      const pe = document.createElement("div");
      pe.className = "page" + (p.deleted ? " deleted" : "") + (p.blank_suggested && !p.deleted ? " blank" : "")
        + (S.pages[sel] && S.pages[sel].id === p.id ? " sel" : "");
      pe.dataset.id = p.id;
      pe.innerHTML = `<img src="/thumbs/${p.file}" style="transform:rotate(${p.rotation}deg)">`
        + `<span class="tag">${p.side === "back" ? "R" : "V"}${p.blank_suggested ? " leer?" : ""}${p.page_of ? ` ${p.page_of[0]}/${p.page_of[1]}` : ""}</span>`;
      pe.onclick = () => { sel = S.pages.findIndex(q => q.id === p.id); render(); };
      el.appendChild(pe);
    });
    strip.appendChild(el);
  });
  $("#info").textContent = `${S.name} · ${S.pages.length} Seiten · ${S.documents.length} Dokument(e)`;
  const s = document.querySelector(".page.sel"); if (s) s.scrollIntoView({block: "nearest"});
}

async function update(ids, fields) { S = await api("/api/update", {ids, ...fields}); render(); }

document.addEventListener("keydown", async e => {
  if (!S || !S.pages.length) return;
  const p = S.pages[sel];
  if ($("#zoom").hidden === false && e.key !== " ") { $("#zoom").hidden = true; return; }
  switch (e.key) {
    case "ArrowRight": sel = Math.min(sel + 1, S.pages.length - 1); render(); break;
    case "ArrowLeft": sel = Math.max(sel - 1, 0); render(); break;
    case "c": {
      // cut belongs to the sheet's first page so front and back stay together
      const first = S.pages.find(q => q.sheet === p.sheet);
      update([first.id], {cut_before: !first.cut_before}); break; }
    case "x": update([p.id], {deleted: !p.deleted}); break;
    case "r": update([p.id], {rotation: (p.rotation + 90) % 360}); break;
    case "n": {
      const name = prompt("Dokumentname (leer = Standard)", p.doc_name || "");
      if (name !== null) update([p.id], {doc_name: name}); break; }
    case "C": update(S.pages.filter(q => q.cut_suggested && !q.cut_before).map(q => q.id), {cut_before: true}); break;
    case "u": S = await api("/api/undo", {}); render(); break;
    case "B": update(S.pages.filter(q => q.blank_suggested && !q.deleted).map(q => q.id), {deleted: true}); break;
    case " ": {
      e.preventDefault();
      const z = $("#zoom"); z.hidden = !z.hidden;
      if (!z.hidden) { const i = z.querySelector("img"); i.src = "/pages/" + p.file; i.style.transform = `rotate(${p.rotation}deg)`; }
      break; }
  }
});

async function doExport(upload) {
  const r = await api("/api/export", {upload});
  toast(`${r.exported.length} PDF(s) exportiert` + (upload ? `, ${r.uploaded.length} hochgeladen, ${r.failed.length} fehlgeschlagen` : ""));
}
$("#export").onclick = () => doExport(false);
$("#upload").onclick = () => doExport(true);
api("/api/session").then(s => { S = s; render(); });
