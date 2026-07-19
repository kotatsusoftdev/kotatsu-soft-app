const fs = require('fs');
const path = require('path');
const https = require('https');
const { execSync } = require('child_process');

// 後で実際のDify APIキーに置き換えてください。
const DIFY_API_KEY = process.env.DIFY_API_KEY || 'YOUR_DIFY_API_KEY';

const cssPath = path.join(__dirname, 'style.css');

function extractJsonObjectFromAnswer(answer) {
  if (typeof answer !== 'string') {
    return null;
  }

  const trimmed = answer.trim();

  try {
    return JSON.parse(trimmed);
  } catch (error) {
    // continue
  }

  const fencedMatch = trimmed.match(/```(?:json)?\s*([\s\S]*?)```/i);
  const candidate = fencedMatch ? fencedMatch[1].trim() : trimmed;

  try {
    return JSON.parse(candidate);
  } catch (error) {
    // continue
  }

  const objectMatch = candidate.match(/\{[\s\S]*\}/);
  if (objectMatch) {
    try {
      return JSON.parse(objectMatch[0]);
    } catch (error) {
      // continue
    }
  }

  return null;
}

function toCssUrl(value) {
  if (typeof value !== 'string') {
    return value;
  }

  const trimmed = value.trim();
  if (!trimmed) {
    return value;
  }

  if (/^url\(/i.test(trimmed)) {
    return trimmed;
  }

  const safeValue = trimmed.replace(/['"\s]+/g, '-').slice(0, 80);
  return `url('https://picsum.photos/seed/${encodeURIComponent(safeValue)}/800/600')`;
}

async function downloadAndProcessImage(promptText, outputFileName) {
  // 将来的に実際の画像生成APIや背景透過処理へ差し替えるためのモック枠組みです。
  return {
    promptText,
    outputFileName,
    imageUrl: 'https://picsum.photos/800/600'
  };
}

function updateCssWithVariables(cssContent, cssVariables) {
  return cssContent.replace(/:root\s*\{([\s\S]*?)\n\}/, (match, block) => {
    const updatedBlock = block.replace(/(--theme-color|--bg-image|--block-skin)\s*:\s*([^;]+);/g, (propertyMatch, variableName) => {
      const newValue = cssVariables[variableName];
      if (newValue !== undefined) {
        const normalizedValue = variableName === '--theme-color' ? newValue : toCssUrl(newValue);
        return `${variableName}: ${normalizedValue};`;
      }
      return propertyMatch;
    });

    return `:root {${updatedBlock}
}`;
  });
}

function runGitDeployment() {
  const gitCommands = [
    'git add style.css',
    'git commit -m "[Kotatsu Studio] Auto-skin update via Dify API"',
    'git push origin main'
  ];

  try {
    for (const command of gitCommands) {
      execSync(command, { cwd: __dirname, stdio: 'inherit' });
    }
    console.log('[Kotatsu Automation] GitHub Pagesへのデプロイコマンドを送信しました！数分で公開サイトが更新されます。');
  } catch (error) {
    console.error('[Kotatsu Automation] Git操作でエラーが発生しました。');
    console.error(error.message);
    process.exit(1);
  }
}

function requestDifySkinData() {
  const requestBody = JSON.stringify({
    inputs: {},
    query: 'イチゴテーマのマージパズルゲームを作って',
    response_mode: 'blocking',
    user: 'kotatsu-soft-owner'
  });

  const options = {
    hostname: 'api.dify.ai',
    path: '/v1/chat-messages',
    method: 'POST',
    headers: {
      Authorization: `Bearer ${DIFY_API_KEY}`,
      'Content-Type': 'application/json'
    }
  };

  const req = https.request(options, (res) => {
    let responseData = '';

    res.setEncoding('utf8');
    res.on('data', (chunk) => {
      responseData += chunk;
    });

    res.on('end', () => {
      if (res.statusCode !== 200) {
        console.error(`[Kotatsu Automation] Dify APIからエラーを受け取りました: ${res.statusCode}`);
        console.error(responseData);
        process.exit(1);
      }

      let parsedResponse;
      try {
        parsedResponse = JSON.parse(responseData);
      } catch (error) {
        console.error(`[Kotatsu Automation] Dify APIのレスポンスをJSONとして解析できませんでした: ${error.message}`);
        process.exit(1);
      }

      const extracted = extractJsonObjectFromAnswer(parsedResponse.answer);
      if (!extracted || !extracted.css_variables) {
        console.error('[Kotatsu Automation] Difyレスポンスから css_variables を取得できませんでした。');
        process.exit(1);
      }

      fs.readFile(cssPath, 'utf8', (readErr, cssContent) => {
        if (readErr) {
          console.error(`[Kotatsu Automation] CSSファイルの読み込みに失敗しました: ${readErr.message}`);
          process.exit(1);
        }

        const updatedCss = updateCssWithVariables(cssContent, extracted.css_variables);

        fs.writeFile(cssPath, updatedCss, 'utf8', (writeErr) => {
          if (writeErr) {
            console.error(`[Kotatsu Automation] CSSファイルの書き込みに失敗しました: ${writeErr.message}`);
            process.exit(1);
          }

          console.log('[Kotatsu Automation] スキンを更新しました！');
          runGitDeployment();
        });
      });
    });
  });

  req.on('error', (error) => {
    console.error(`[Kotatsu Automation] Dify APIへの接続に失敗しました: ${error.message}`);
    process.exit(1);
  });

  req.write(requestBody);
  req.end();
}

requestDifySkinData();
