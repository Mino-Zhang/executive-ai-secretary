import { startProdServer } from "vinext/server/prod-server";

const port = Number.parseInt(process.env.PORT ?? "3000", 10);
const host = process.env.HOST ?? "0.0.0.0";
const outDir = process.env.VINEXT_OUT_DIR ?? "/app/dist";

if (!Number.isInteger(port) || port < 1 || port > 65535) {
  throw new Error("PORT must be an integer from 1 to 65535");
}

await startProdServer({
  port,
  host,
  outDir,
  purpose: "production",
});
