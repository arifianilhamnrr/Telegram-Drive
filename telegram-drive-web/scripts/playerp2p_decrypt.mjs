#!/usr/bin/env node
/**
 * Decrypt playerp2p.live API responses by reusing crypto helpers from the live player bundle.
 * Hardcoded x()/R() break whenever the obfuscator rotates — we eval the bundle block instead.
 */
import fs from "fs";
import https from "https";
import { webcrypto } from "crypto";

const jsPath = process.env.P2P_JS_PATH || "/tmp/p2p.js";
if (!fs.existsSync(jsPath)) {
  throw new Error(`player JS not found: ${jsPath} (fetch embed page first)`);
}
const js = fs.readFileSync(jsPath, "utf8");

function loadStringTable(source) {
  const pnStart = source.indexOf("function pn(){const i=[");
  if (pnStart < 0) {
    throw new Error("function pn() string table not found in player JS");
  }
  const pnEnd = source.indexOf("return pn=function(){return i},pn()}", pnStart);
  if (pnEnd < 0) {
    throw new Error("pn() end marker not found in player JS");
  }
  const tail = "return pn=function(){return i},pn()}";
  let arr;
  // eslint-disable-next-line no-eval
  eval(source.slice(pnStart, pnEnd + tail.length) + "; arr = pn();");
  if (!Array.isArray(arr)) {
    throw new Error("failed to load playerp2p string table");
  }

  const targetMatch = source.match(
    /parseInt\(t\(246\)\)\s*\/\s*12\)\s*===\s*(\d+)/
  );
  const target = targetMatch ? parseInt(targetMatch[1], 10) : 529585;

  function checksum(a) {
    const t = (i) => a[i - 131];
    try {
      return (
        (parseInt(t(142)) / 1) * (parseInt(t(353)) / 2) +
          (-parseInt(t(412)) / 3) +
          (parseInt(t(371)) / 4) * (-parseInt(t(237)) / 5) +
          parseInt(t(191)) / 6 +
          (parseInt(t(338)) / 7) * (parseInt(t(340)) / 8) +
          (-parseInt(t(471)) / 9) * (-parseInt(t(261)) / 10) +
          (-parseInt(t(507)) / 11) * (parseInt(t(246)) / 12) ===
        target
      );
    } catch {
      return false;
    }
  }

  let rot = 0;
  while (!checksum(arr) && rot < arr.length + 50) {
    arr.push(arr.shift());
    rot++;
  }
  if (!checksum(arr)) {
    throw new Error("playerp2p string table rotation failed (checksum mismatch)");
  }
  return (i) => arr[i - 131];
}

function extractCryptoBlock(source) {
  const anchor = source.indexOf("subtle[o(393)](o(306),x()");
  if (anchor < 0) {
    throw new Error("AES decrypt call not found in player JS");
  }
  const blockStart = source.lastIndexOf("J=r=>{", anchor);
  const blockEnd = source.indexOf("},$=async", anchor);
  if (blockStart < 0 || blockEnd < 0) {
    throw new Error("crypto helper block not found in player JS");
  }
  return source.slice(blockStart, blockEnd + 1);
}

const videoId = process.argv[2] || "";
const host = process.argv[3] || "ewa.playerp2p.live";
const ep = process.argv[4] || "download";

if (!videoId) {
  throw new Error("video id required (hash fragment or ?id=)");
}

const j = loadStringTable(js);

global.window = {
  location: {
    hash: `#${videoId}`,
    origin: `https://${host}`,
    protocol: "https:",
    hostname: host,
  },
  crypto: webcrypto,
  TextEncoder,
  TextDecoder,
};

const cryptoBlock = extractCryptoBlock(js);
// Bundle helpers use comma-assignments (J=r=>,k=,…,N=async) — must run in sloppy
// function scope; ESM eval() is strict and throws ReferenceError.
const N = new Function(
  "j",
  "window",
  `${cryptoBlock}; return N;`
)(j, global.window);

function fetchText(url) {
  return new Promise((resolve, reject) => {
    https
      .get(
        url,
        {
          headers: {
            "User-Agent": "Mozilla/5.0",
            Referer: "https://bridgestoabrighterfuture.org/",
          },
        },
        (res) => {
          let d = "";
          res.on("data", (c) => (d += c));
          res.on("end", () => resolve(d));
        }
      )
      .on("error", reject);
  });
}

const url = `https://${host}/api/v1/${ep}?id=${videoId}`;
const hex = (await fetchText(url)).trim();
if (!/^[0-9a-f]+$/i.test(hex)) {
  throw new Error(`unexpected API response (not hex ciphertext): ${hex.slice(0, 80)}`);
}
const text = await N(hex);
console.log(text);