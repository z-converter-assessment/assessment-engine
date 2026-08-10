const slides = [...document.querySelectorAll(".slide")];
const position = document.querySelector("#deck-position");
const previousButton = document.querySelector('[data-action="prev"]');
const nextButton = document.querySelector('[data-action="next"]');

let currentIndex = 0;

function clampIndex(index) {
  return Math.max(0, Math.min(slides.length - 1, index));
}

function hashFor(index) {
  const slide = slides[index];
  return slide.dataset.section === "appendix" ? `a${slide.dataset.number}` : slide.dataset.number;
}

function indexFromHash() {
  const raw = window.location.hash.replace(/^#/, "").toLowerCase();
  if (!raw) return 0;

  const appendixMatch = raw.match(/^a(\d+)$/);
  if (appendixMatch) {
    const number = Number(appendixMatch[1]);
    return slides.findIndex((slide) => slide.dataset.section === "appendix" && Number(slide.dataset.number) === number);
  }

  const number = Number(raw);
  if (Number.isInteger(number) && number > 0) {
    return slides.findIndex((slide) => slide.dataset.section === "main" && Number(slide.dataset.number) === number);
  }
  return 0;
}

function updateScale() {
  const widthScale = window.innerWidth / 1280;
  const heightScale = window.innerHeight / 720;
  document.documentElement.style.setProperty("--deck-scale", String(Math.min(widthScale, heightScale)));
}

function goTo(index, options = {}) {
  currentIndex = clampIndex(index);
  slides.forEach((slide, slideIndex) => slide.classList.toggle("active", slideIndex === currentIndex));
  position.textContent = `${currentIndex + 1} / ${slides.length}`;
  previousButton.disabled = currentIndex === 0;
  nextButton.disabled = currentIndex === slides.length - 1;

  if (options.updateHash !== false) {
    window.history.replaceState(null, "", `#${hashFor(currentIndex)}`);
  }
}

function move(delta) {
  goTo(currentIndex + delta);
}

document.addEventListener("keydown", (event) => {
  if (["ArrowRight", "ArrowDown", "PageDown", " "].includes(event.key)) {
    event.preventDefault();
    move(1);
  } else if (["ArrowLeft", "ArrowUp", "PageUp"].includes(event.key)) {
    event.preventDefault();
    move(-1);
  } else if (event.key === "Home") {
    event.preventDefault();
    goTo(0);
  } else if (event.key === "End") {
    event.preventDefault();
    goTo(slides.length - 1);
  }
});

previousButton.addEventListener("click", () => move(-1));
nextButton.addEventListener("click", () => move(1));
window.addEventListener("resize", updateScale);
window.addEventListener("hashchange", () => goTo(indexFromHash(), { updateHash: false }));

window.Deck = {
  count: slides.length,
  goTo,
  getIndex: () => currentIndex,
};

updateScale();
goTo(indexFromHash(), { updateHash: false });
