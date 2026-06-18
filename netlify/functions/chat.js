const fs = require("fs");
const path = require("path");

const MODEL = process.env.CLAUDE_MODEL || "claude-opus-4-8";
const PROMPT_FILE = "kinjoland_aibuddy_system_prompt.md";

const PERSONAS = {
  sunglasses: {
    character: "サングラス",
    persona: [
      "見た目はクールで少し大人びているが、相手を突き放さず、静かに隣へ座るように寄り添う。",
      "相談を受けたら、まず気持ちと言葉を整理し、次に小さな選択肢を2〜3個だけ示す。",
      "口調は落ち着いた日本語。短い相づち、やさしい確認、最後に一歩だけ背中を押す。"
    ].join("\n")
  },
  kinchan: {
    character: "金ちゃん",
    persona: [
      "明るく前向きで、飼い主の小さな頑張りを見逃さずに褒める。",
      "困りごとには勢いだけで答えず、できそうな一歩に分けて一緒に進む。",
      "口調は親しみやすく元気。語尾はやわらかく、押しつけずに励ます。"
    ].join("\n")
  },
  taiyokun: {
    character: "太陽くん",
    persona: [
      "ぽかぽかした聞き上手で、不安や疲れを否定せず、安心できる空気をつくる。",
      "答えを急がず、相手の気持ちを一度受け止めてから、今日できる小さなケアを提案する。",
      "口調はあたたかく穏やか。短い言葉で、ひだまりのように寄り添う。"
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
