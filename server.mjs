import crypto from "node:crypto";
import fs from "node:fs";
import fsp from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { fileURLToPath, pathToFileURL } from "node:url";

const execFileAsync = promisify(execFile);
const ROOT = path.dirname(fileURLToPath(import.meta.url));
const DOWNLOADS_DIR = path.resolve(process.env.SLIDES_DIR || path.join(ROOT, "downloads"));
const CSV_PATH = path.resolve(process.env.SLIDES_CSV || path.join(DOWNLOADS_DIR, "slides.csv"));
const DIST_DIR = path.join(ROOT, "dist");
const PORT = Number(process.env.PORT || 4173);
const MAX_IMAGES = 300;
const MAX_IMAGE_SIZE = 24 * 1024 * 1024;
const IMAGE_RE = /\.(png|jpe?g|webp|gif)$/i;
const STATIC_TYPES = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg", ".webp": "image/webp", ".ico": "image/x-icon",
};

let catalog = new Map();

function naturalCompare(a, b) {
  return a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
}

function displayTitle(filename) {
  return path.basename(filename, path.extname(filename))
    .replace(/[-_]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function templateId(zipPath) {
  return crypto.createHash("sha1").update(path.resolve(zipPath).toLowerCase()).digest("hex").slice(0, 16);
}

function parseCsv(text) {
  const matrix = [];
  let row = [], field = "", quoted = false;
  const source = text.replace(/^\uFEFF/, "");
  for (let i = 0; i <= source.length; i += 1) {
    const char = source[i] ?? "\n";
    if (quoted) {
      if (char === '"' && source[i + 1] === '"') { field += '"'; i += 1; }
      else if (char === '"') quoted = false;
      else field += char;
    } else if (char === '"') quoted = true;
    else if (char === ",") { row.push(field); field = ""; }
    else if (char === "\n") {
      row.push(field.replace(/\r$/, "")); field = "";
      if (row.some((cell) => cell !== "")) matrix.push(row);
      row = [];
    } else field += char;
  }
  const headers = matrix.shift() || [];
  return matrix.map((values) => Object.fromEntries(headers.map((header, index) => [header, values[index] || ""])));
}

async function walkZipFiles(dir) {
  const result = [];
  async function walk(current) {
    let entries;
    try { entries = await fsp.readdir(current, { withFileTypes: true }); } catch { return; }
    for (const entry of entries) {
      const fullPath = path.join(current, entry.name);
      if (entry.isDirectory()) await walk(fullPath);
      else if (entry.isFile() && entry.name.toLowerCase().endsWith(".zip")) result.push(fullPath);
    }
  }
  await walk(dir);
  return result;
}

async function readCsvRows() {
  try { return parseCsv(await fsp.readFile(CSV_PATH, "utf8")); } catch { return []; }
}

function csvFilePath(row) {
  const link = row["파일 경로 링크"] || "";
  if (link.startsWith("file:")) {
    try { return path.resolve(fileURLToPath(link)); } catch { return ""; }
  }
  return link ? path.resolve(link) : "";
}

async function refreshCatalog() {
  const [zipFiles, csvRows] = await Promise.all([walkZipFiles(DOWNLOADS_DIR), readCsvRows()]);
  const csvByPath = new Map(csvRows.map((row) => [csvFilePath(row).toLowerCase(), row]));
  const next = new Map();
  for (const zipPath of zipFiles.sort(naturalCompare)) {
    const resolved = path.resolve(zipPath);
    const relative = path.relative(DOWNLOADS_DIR, resolved);
    if (relative.startsWith("..") || path.isAbsolute(relative)) continue;
    const row = csvByPath.get(resolved.toLowerCase()) || {};
    const parts = relative.split(path.sep);
    const stat = await fsp.stat(resolved);
    const id = templateId(resolved);
    next.set(id, {
      id, zipPath: resolved,
      category: parts.length > 1 ? parts[0] : "기타",
      title: row["썸네일 제목"] || displayTitle(resolved),
      filename: path.basename(resolved),
      filePathLink: row["파일 경로 링크"] || pathToFileURL(resolved).href,
      sourceThumbnail: row["썸네일 이미지"] || "",
      size: stat.size,
      modifiedAt: stat.mtime.toISOString(),
    });
  }
  catalog = next;
  return [...catalog.values()];
}

async function imageEntries(template) {
  const { stdout } = await execFileAsync("tar", ["-tf", template.zipPath], { encoding: "utf8", maxBuffer: 8 * 1024 * 1024 });
  const all = stdout.split(/\r?\n/).filter((name) => IMAGE_RE.test(name));
  const previews = all.filter((name) => /(^|\/)previews?\//i.test(name));
  const thumbnails = all.filter((name) => /(^|\/)thumbnails?\//i.test(name));
  const selected = previews.length ? previews : thumbnails.length ? thumbnails : all;
  return selected.sort(naturalCompare).slice(0, MAX_IMAGES);
}

async function extractImage(template, entryName) {
  const { stdout } = await execFileAsync("tar", ["-xOf", template.zipPath, entryName], {
    encoding: "buffer", maxBuffer: MAX_IMAGE_SIZE,
  });
  return stdout;
}

function imageMime(filename) {
  const ext = path.extname(filename).toLowerCase();
  return ({ ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif" })[ext] || "application/octet-stream";
}

function publicTemplate(item) {
  return {
    id: item.id, category: item.category, title: item.title, filename: item.filename,
    filePathLink: item.filePathLink, size: item.size, modifiedAt: item.modifiedAt,
    thumbnailUrl: item.sourceThumbnail || `/api/templates/${item.id}/thumbnail`,
    slidesUrl: `/api/templates/${item.id}/slides`,
    downloadUrl: `/api/templates/${item.id}/download`,
  };
}

function sendJson(res, status, body) {
  const data = Buffer.from(JSON.stringify(body));
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Content-Length": data.length });
  res.end(data);
}

async function templateFor(id) {
  if (!catalog.size) await refreshCatalog();
  return catalog.get(id);
}

async function serveApi(req, res, pathname) {
  if (pathname === "/api/templates") {
    const items = await refreshCatalog();
    return sendJson(res, 200, { templates: items.map(publicTemplate), csvPath: CSV_PATH, downloadsPath: DOWNLOADS_DIR });
  }
  let match = pathname.match(/^\/api\/templates\/([a-f0-9]+)\/slides$/);
  if (match) {
    const item = await templateFor(match[1]);
    if (!item) return sendJson(res, 404, { error: "템플릿을 찾을 수 없습니다." });
    const entries = await imageEntries(item);
    return sendJson(res, 200, {
      template: publicTemplate(item),
      slides: entries.map((name, index) => ({ index, name: path.basename(name), url: `/api/templates/${item.id}/assets/${index}` })),
    });
  }
  match = pathname.match(/^\/api\/templates\/([a-f0-9]+)\/(thumbnail|assets\/(\d+))$/);
  if (match) {
    const item = await templateFor(match[1]);
    if (!item) return sendJson(res, 404, { error: "템플릿을 찾을 수 없습니다." });
    const entries = await imageEntries(item);
    const index = match[2] === "thumbnail" ? 0 : Number(match[3]);
    const entry = entries[index];
    if (!entry) return sendJson(res, 404, { error: "미리보기 이미지를 찾을 수 없습니다." });
    const image = await extractImage(item, entry);
    res.writeHead(200, { "Content-Type": imageMime(entry), "Content-Length": image.length, "Cache-Control": "private, max-age=3600" });
    return res.end(image);
  }
  match = pathname.match(/^\/api\/templates\/([a-f0-9]+)\/download$/);
  if (match) {
    const item = await templateFor(match[1]);
    if (!item || !fs.existsSync(item.zipPath)) return sendJson(res, 404, { error: "ZIP을 찾을 수 없습니다." });
    const safeAscii = item.filename.replace(/[^\x20-\x7E]/g, "_").replace(/["\\]/g, "_");
    res.writeHead(200, {
      "Content-Type": "application/zip", "Content-Length": item.size,
      "Content-Disposition": `attachment; filename="${safeAscii}"; filename*=UTF-8''${encodeURIComponent(item.filename)}`,
    });
    return fs.createReadStream(item.zipPath).pipe(res);
  }
  return sendJson(res, 404, { error: "API 경로를 찾을 수 없습니다." });
}

async function serveStatic(res, pathname) {
  let requested = pathname === "/" ? "index.html" : decodeURIComponent(pathname.slice(1));
  let filePath = path.resolve(DIST_DIR, requested);
  if (!filePath.startsWith(DIST_DIR + path.sep)) filePath = path.join(DIST_DIR, "index.html");
  try {
    const stat = await fsp.stat(filePath);
    if (!stat.isFile()) throw new Error("not a file");
  } catch {
    filePath = path.join(DIST_DIR, "index.html");
  }
  try {
    const data = await fsp.readFile(filePath);
    res.writeHead(200, { "Content-Type": STATIC_TYPES[path.extname(filePath).toLowerCase()] || "application/octet-stream", "Content-Length": data.length });
    res.end(data);
  } catch {
    sendJson(res, 503, { error: "프론트엔드가 아직 빌드되지 않았습니다. npm run build를 실행하세요." });
  }
}

const server = http.createServer(async (req, res) => {
  const pathname = new URL(req.url || "/", `http://${req.headers.host || "localhost"}`).pathname;
  try {
    if (pathname.startsWith("/api/")) await serveApi(req, res, pathname);
    else await serveStatic(res, pathname);
  } catch (error) {
    sendJson(res, 500, { error: `요청을 처리하지 못했습니다: ${error.message}` });
  }
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`Slide Library: http://127.0.0.1:${PORT}`);
  console.log(`Downloads: ${DOWNLOADS_DIR}`);
});
