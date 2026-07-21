import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { createServer } from "node:http";
import { extname, resolve, sep } from "node:path";

const root = resolve("dist");
const contentTypes = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
};

let idleTimer;
const closeWhenIdle = () => {
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => server.close(), 5_000);
};

const server = createServer(async (request, response) => {
  closeWhenIdle();
  const pathname = decodeURIComponent(new URL(request.url ?? "/", "http://localhost").pathname);
  const requested = resolve(root, `.${pathname}`);
  let file = requested.startsWith(`${root}${sep}`) || requested === root ? requested : root;

  try {
    if ((await stat(file)).isDirectory()) file = resolve(file, "index.html");
    if (!(await stat(file)).isFile()) throw new Error("not a file");
  } catch {
    file = resolve(root, "index.html");
  }

  response.setHeader("Content-Type", contentTypes[extname(file)] ?? "application/octet-stream");
  createReadStream(file).pipe(response);
});

server.listen(5173, "127.0.0.1", closeWhenIdle);
