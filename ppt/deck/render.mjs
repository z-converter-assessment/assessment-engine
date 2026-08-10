import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { chromium } from "playwright";

const deckDir = path.dirname(new URL(import.meta.url).pathname);
const repoRoot = path.resolve(deckDir, "../..");
const manifestPath = path.join(deckDir, "assets/screenshots/manifest.json");
const outputDir = "/tmp/assessment-deck-render";
const slideOutputDir = path.join(outputDir, "slides");
const pdfPath = path.join(repoRoot, "ppt/ZConverter_Assessment_2team.pdf");

await fs.mkdir(slideOutputDir, { recursive: true });

const browser = await chromium.launch({ headless: true });

async function generateSanitizedScreenshots() {
  const manifest = JSON.parse(await fs.readFile(manifestPath, "utf8"));
  const fontPath = path.join(deckDir, "assets/fonts/noto-sans-kr-korean-500.woff2");
  const fontData = (await fs.readFile(fontPath)).toString("base64");

  for (const [name, definition] of Object.entries(manifest)) {
    const sourcePath = path.resolve(path.dirname(manifestPath), definition.source);
    const sourceData = (await fs.readFile(sourcePath)).toString("base64");
    const page = await browser.newPage({
      viewport: { width: definition.crop.width, height: definition.crop.height },
      deviceScaleFactor: 1,
    });

    const overlays = definition.redactions
      .map(
        (redaction) => `<div class="redaction" style="left:${redaction.x}px;top:${redaction.y}px;width:${redaction.width}px;height:${redaction.height}px">${redaction.text}</div>`,
      )
      .join("");

    await page.setContent(`
      <!doctype html>
      <html lang="ko">
      <head>
        <meta charset="utf-8">
        <style>
          @font-face { font-family: "Noto Sans KR"; src: url(data:font/woff2;base64,${fontData}) format("woff2"); font-weight: 500; }
          * { box-sizing: border-box; }
          html, body { width: 100%; height: 100%; margin: 0; overflow: hidden; background: #fff; }
          img { position: absolute; left: -${definition.crop.x}px; top: -${definition.crop.y}px; max-width: none; }
          .redaction { position: absolute; display: flex; align-items: center; padding: 0 14px; background: #fff; color: #26384b; font: 500 25px/1.25 "Noto Sans KR", sans-serif; }
        </style>
      </head>
      <body>
        <img src="data:image/png;base64,${sourceData}" alt="">
        ${overlays}
      </body>
      </html>
    `);
    await page.waitForFunction(() => document.images[0]?.complete === true);
    await page.screenshot({ path: path.join(deckDir, `assets/screenshots/${name}.png`) });
    await page.close();
  }
}

async function renderDeck() {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1 });
  const externalRequests = [];
  page.on("request", (request) => {
    if (/^https?:/.test(request.url())) externalRequests.push(request.url());
  });

  await page.goto(pathToFileURL(path.join(deckDir, "index.html")).href, { waitUntil: "load" });
  await page.evaluate(() => document.fonts.ready);

  const slideCount = await page.evaluate(() => window.Deck.count);
  const fontLoaded = await page.evaluate(() => document.fonts.check('22px "Noto Sans KR"', "가나다 ABC"));
  const overflow = [];

  async function prepareSlide(index) {
    await page.evaluate((slideIndex) => window.Deck.goTo(slideIndex, { updateHash: false }), index);
    await page.evaluate(async () => {
      const text = document.querySelector(".slide.active")?.textContent || "";
      await Promise.all([400, 500, 700, 800].map((weight) => document.fonts.load(`${weight} 39px "Noto Sans KR"`, text)));
      await document.fonts.ready;
    });
    await page.waitForTimeout(250);
  }

  for (let index = 0; index < slideCount; index += 1) {
    await prepareSlide(index);
  }

  for (let index = 0; index < slideCount; index += 1) {
    await prepareSlide(index);
    const issues = await page.evaluate(() => {
      const slide = document.querySelector(".slide.active");
      const slideRect = slide.getBoundingClientRect();
      return [...slide.querySelectorAll("*")]
        .filter((element) => element.getAttribute("aria-hidden") !== "true" && element.offsetParent !== null)
        .map((element) => {
          const rect = element.getBoundingClientRect();
          return {
            tag: element.tagName,
            text: (element.textContent || "").trim().replace(/\s+/g, " ").slice(0, 60),
            left: rect.left - slideRect.left,
            top: rect.top - slideRect.top,
            right: rect.right - slideRect.left,
            bottom: rect.bottom - slideRect.top,
          };
        })
        .filter((rect) => rect.left < -1 || rect.top < -1 || rect.right > 1281 || rect.bottom > 721);
    });
    if (issues.length > 0) overflow.push({ slide: index + 1, issues });
    await page.screenshot({ type: "png" });
    await page.waitForTimeout(100);
    await page.screenshot({ path: path.join(slideOutputDir, `slide-${String(index + 1).padStart(2, "0")}.png`) });
  }

  await page.emulateMedia({ media: "print" });
  await page.pdf({ path: pdfPath, printBackground: true, preferCSSPageSize: true });
  const pdfData = await fs.readFile(pdfPath);
  const pageObjects = pdfData.toString("latin1").match(/\/Type\s*\/Page(?!s)/g) || [];

  await page.close();
  return { slideCount, fontLoaded, externalRequests, overflow, pdfPageObjects: pageObjects.length };
}

async function createContactSheet(slideCount) {
  const columns = 4;
  const thumbWidth = 288;
  const thumbHeight = 162;
  const gap = 18;
  const labelHeight = 24;
  const rows = Math.ceil(slideCount / columns);
  const width = columns * thumbWidth + (columns + 1) * gap;
  const height = rows * (thumbHeight + labelHeight) + (rows + 1) * gap;
  const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });

  const images = [];
  for (let index = 0; index < slideCount; index += 1) {
    const imagePath = path.join(slideOutputDir, `slide-${String(index + 1).padStart(2, "0")}.png`);
    const data = (await fs.readFile(imagePath)).toString("base64");
    images.push(`<figure><img src="data:image/png;base64,${data}" alt="slide ${index + 1}"><figcaption>${index + 1}</figcaption></figure>`);
  }

  await page.setContent(`
    <!doctype html>
    <style>
      * { box-sizing: border-box; }
      body { margin: 0; padding: ${gap}px; background: #d9e0e6; display: grid; grid-template-columns: repeat(${columns}, ${thumbWidth}px); gap: ${gap}px; }
      figure { width: ${thumbWidth}px; margin: 0; }
      img { display: block; width: ${thumbWidth}px; height: ${thumbHeight}px; object-fit: cover; border: 1px solid #8796a3; background: #fff; }
      figcaption { height: ${labelHeight}px; color: #25384a; font: 700 13px/24px sans-serif; text-align: center; }
    </style>
    ${images.join("")}
  `);
  await page.screenshot({ path: path.join(outputDir, "contact-sheet.png"), fullPage: true });
  await page.close();
}

await generateSanitizedScreenshots();
const report = await renderDeck();
await createContactSheet(report.slideCount);
await fs.writeFile(path.join(outputDir, "render-report.json"), `${JSON.stringify(report, null, 2)}\n`, "utf8");
await browser.close();

console.log(JSON.stringify({ pdfPath, contactSheet: path.join(outputDir, "contact-sheet.png"), ...report }, null, 2));
