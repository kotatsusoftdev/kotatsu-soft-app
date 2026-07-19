const stage = document.getElementById('game-stage');
const scoreEl = document.getElementById('score');
let score = 0;

function getSizeByLevel(level) {
  return 30 + level * 18;
}

function createFruit(x, y) {
  const level = Math.min(5, Math.max(1, Math.floor(Math.random() * 5) + 1));
  const size = getSizeByLevel(level);

  const fruit = document.createElement('button');
  fruit.className = 'fruit-block';
  fruit.type = 'button';
  fruit.dataset.level = String(level);
  fruit.style.width = `${size}px`;
  fruit.style.height = `${size}px`;
  fruit.style.left = `${x}px`;
  fruit.style.top = `${y}px`;
  fruit.textContent = level;

  fruit.addEventListener('click', (event) => {
    event.stopPropagation();

    const currentLevel = Number(fruit.dataset.level);
    if (currentLevel < 5) {
      const nextLevel = currentLevel + 1;
      fruit.dataset.level = String(nextLevel);
      const nextSize = getSizeByLevel(nextLevel);
      fruit.style.width = `${nextSize}px`;
      fruit.style.height = `${nextSize}px`;
      fruit.textContent = nextLevel;
      score += nextLevel;
    } else {
      score += 1;
    }

    scoreEl.textContent = String(score);
  });

  stage.appendChild(fruit);
}

stage.addEventListener('click', (event) => {
  if (event.target !== stage) {
    return;
  }

  const rect = stage.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  createFruit(x, y);
});
