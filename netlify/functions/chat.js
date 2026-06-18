const fs = require("fs");
const path = require("path");

const MODEL = process.env.CLAUDE_MODEL || "claude-opus-4-8";
const PROMPT_FILE = "kinjoland_aibuddy_system_prompt.md";

const PERSONAS = {
  sunglasses: {
    character: "祈って解決するサングラス",
    persona: [
      "### 祈って解決するサングラス",
      "- 一人称・口調: 落ち着いた、静かで優しい話し方。",
      "- 雰囲気: ミステリアスで頼れる兄貴分/姉御肌。困った人とまず「一緒に祈ろう(=落ち着いて考えよう)」と促す。",
      "- 決め所: 本気のときサングラスの奥の素顔(真剣な表情)を見せるイメージ。祈り=冷静さを取り戻すスイッチ。",
      "- 得意: 重い悩み・追い詰められた人を、静かに受け止めて落ち着かせる。"
    ].join("\n")
  },
  kinchan: {
    character: "金ちゃん",
    persona: [
      "### 金ちゃん(KINJOLAND看板)",
      "- 一人称・口調: 元気で明るい、親しみやすい話し方。",
      "- 雰囲気: いつも前向き。「大丈夫だって!なんとかなるよ!」と明るく背中を押す。",
      "- 得意: 落ち込んでいる人を明るく励ます。最初の一歩を踏み出させる。"
    ].join("\n")
  },
  taiyokun: {
    character: "太陽くん",
    persona: [
      "### 太陽くん",
      "- 一人称・口調: あたたかく、包み込むような話し方。",
      "- 雰囲気: 太陽のように、そばにいるだけで安心する存在。",
      "- 得意: 孤独・寂しさを抱えた人に寄り添い、ぬくもりを与える。"
    ].join("\n")
  }
};

exports.handler = async (event) => {
  if (event.httpMethod !== "POST") {
    return json(405, { error: "POST only" });
  }

  try {
    const apiKey = process.env.ANTHROPIC_API_KEY || process.env.CLAUDE_API_KEY;
    if (!apiKey) {
      return json(500, { error: "Netlify環境変数 ANTHROPIC_API_KEY を設定してください。" });
    }

    const body = JSON.parse(event.body || "{}");
    const personaConfig = PERSONAS[body.characterId];
    if (!personaConfig) {
      return json(400, { error: "選択されたキャラが不正です。" });
    }

    const name = sanitizeName(body.name);
    if (!name) {
      return json(400, { error: "相棒の名前を入力してください。" });
    }

    const messages = normalizeMessages(body.messages);
    if (!messages.length || messages[messages.length - 1].role !== "user") {
      return json(400, { error: "ユーザーの相談内容がありません。" });
    }

    const system = buildSystemPrompt({
      character: personaConfig.character,
      name,
      persona: personaConfig.persona
    });

    const response = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-api-key": apiKey,
        "anthropic-version": "2023-06-01"
      },
      body: JSON.stringify({
        model: MODEL,
        max_tokens: 1200,
        system,
        messages
      })
    });

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = data && data.error && data.error.message ? data.error.message : `Claude API error: HTTP ${response.status}`;
      return json(response.status, { error: message });
    }

    const reply = Array.isArray(data.content)
      ? data.content.filter((block) => block.type === "text").map((block) => block.text).join("\n").trim()
      : "";

    return json(200, { reply });
  } catch (error) {
    return json(500, { error: error.message || "サーバーエラーが発生しました。" });
  }
};

function buildSystemPrompt(values) {
  const template = readPromptTemplate();
  return template
    .replaceAll("{CHARACTER}", values.character)
    .replaceAll("{NAME}", values.name)
    .replaceAll("{PERSONA}", values.persona);
}

function readPromptTemplate() {
  const candidates = [
    path.join(process.cwd(), PROMPT_FILE),
    path.join(__dirname, "..", "..", PROMPT_FILE),
    path.join(__dirname, PROMPT_FILE),
    path.join("/var/task", PROMPT_FILE)
  ];

  for (const file of candidates) {
    if (fs.existsSync(file)) {
      return fs.readFileSync(file, "utf8");
    }
  }

  throw new Error(`${PROMPT_FILE} が見つかりません。`);
}

function normalizeMessages(messages) {
  if (!Array.isArray(messages)) return [];
  return messages
    .filter((message) => message && (message.role === "user" || message.role === "assistant"))
    .map((message) => ({
      role: message.role,
      content: String(message.content || "").slice(0, 4000)
    }))
    .filter((message) => message.content.trim())
    .slice(-24);
}

function sanitizeName(name) {
  return String(name || "").trim().slice(0, 24);
}

function json(statusCode, payload) {
  return {
    statusCode,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store"
    },
    body: JSON.stringify(payload)
  };
}
