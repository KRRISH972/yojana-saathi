/* Yojana Saathi - browser app: chat, scheme cards, voice in and out. No frameworks. */
(function () {
  "use strict";

  var L = window.YSLogic;
  var STORE_KEY = "ys-session-v1"; // sessionStorage: cleared when the tab closes (shared phones)
  var MAX_STORED_MESSAGES = 60;

  var el = {
    log: document.getElementById("log"),
    form: document.getElementById("composer"),
    input: document.getElementById("message"),
    send: document.getElementById("send"),
    mic: document.getElementById("mic"),
    quick: document.getElementById("quick"),
    status: document.getElementById("status"),
    langHi: document.getElementById("lang-hi"),
    langEn: document.getElementById("lang-en"),
    newChat: document.getElementById("new-chat")
  };

  var session = loadSession();
  var busy = false;
  var lastInputWasVoice = false;

  /* ---------- session (conversation state is kept here, not on the server) ---------- */

  function emptySession() {
    return { lang: "hi", state: null, messages: [] };
  }

  function loadSession() {
    try {
      var saved = JSON.parse(sessionStorage.getItem(STORE_KEY) || "null");
      if (saved && Array.isArray(saved.messages)) return saved;
    } catch (e) {
      /* storage blocked or corrupt: start fresh */
    }
    return emptySession();
  }

  function saveSession() {
    try {
      session.messages = session.messages.slice(-MAX_STORED_MESSAGES);
      sessionStorage.setItem(STORE_KEY, JSON.stringify(session));
    } catch (e) {
      /* private mode or full storage: the chat still works, it just won't survive a reload */
    }
  }

  /* ---------- rendering ---------- */

  function node(tag, className, text) {
    var n = document.createElement(tag);
    if (className) n.className = className;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  function appendText(parent, text) {
    L.splitLinks(text).forEach(function (part) {
      if (part.type === "link") {
        var a = node("a", "underline text-emerald-800 break-all", part.value);
        a.href = part.value;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        parent.appendChild(a);
      } else {
        parent.appendChild(document.createTextNode(part.value));
      }
    });
  }

  function speakButton(text, languageStyle) {
    var t = L.texts(session.lang);
    var b = node("button", "mt-2 text-sm text-emerald-800 underline", "🔊 " + t.listen);
    b.type = "button";
    b.addEventListener("click", function () {
      speak(text, languageStyle);
    });
    return b;
  }

  function renderCard(card) {
    var t = L.texts(session.lang);
    var badge = L.statusBadge(card.status, session.lang);
    var box = node("article", "mt-2 rounded-xl border bg-white p-3 shadow-sm");
    box.appendChild(node("h3", "font-semibold text-slate-900", L.schemeName(card, session.lang)));
    box.appendChild(node("p", "mt-1 inline-block rounded-full border px-2 py-0.5 text-sm " + badge.className, badge.label));
    if (card.status !== "not_eligible") {
      var benefits = node("p", "mt-2 text-sm text-slate-700");
      benefits.appendChild(node("span", "font-medium", t.benefits + ": "));
      benefits.appendChild(document.createTextNode(card.benefits));
      box.appendChild(benefits);
      var link = node("a", "mt-2 inline-block rounded-lg bg-emerald-700 px-3 py-2 text-sm font-medium text-white", t.official + " ↗");
      link.href = card.official_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      box.appendChild(link);
    } else if (card.reasons && card.reasons.length) {
      box.appendChild(node("p", "mt-2 text-sm text-slate-600", card.reasons.join(" ")));
    }
    return box;
  }

  function renderMessage(message) {
    var mine = message.role === "user";
    var row = node("div", "flex " + (mine ? "justify-end" : "justify-start"));
    var bubble = node(
      "div",
      "max-w-[85%] whitespace-pre-line rounded-2xl px-4 py-3 text-base leading-relaxed " +
        (mine ? "bg-emerald-700 text-white" : message.error ? "bg-red-50 text-red-900 border border-red-200" : "bg-white text-slate-900 shadow-sm")
    );
    appendText(bubble, mine ? message.text : L.cleanReply(message.text));
    if (!mine && !message.error && message.text) bubble.appendChild(speakButton(message.text, message.languageStyle));
    (message.schemes || []).forEach(function (card) {
      bubble.appendChild(renderCard(card));
    });
    row.appendChild(bubble);
    el.log.appendChild(row);
  }

  function renderWelcome() {
    var t = L.texts(session.lang);
    var box = node("div", "rounded-2xl bg-white p-4 text-slate-900 shadow-sm");
    box.appendChild(node("p", "text-base leading-relaxed", t.welcome));
    var examples = node("div", "mt-3 flex flex-col gap-2");
    t.examples.forEach(function (example) {
      var b = node("button", "rounded-xl border border-emerald-300 bg-emerald-50 px-3 py-2 text-left text-emerald-900", example);
      b.type = "button";
      b.addEventListener("click", function () {
        sendMessage(example, false);
      });
      examples.appendChild(b);
    });
    box.appendChild(examples);
    el.log.appendChild(box);
  }

  function renderQuickReplies() {
    el.quick.textContent = "";
    var field = session.state && session.state.last_question_field;
    L.quickReplies(field, session.lang).forEach(function (reply) {
      var b = node("button", "min-h-12 flex-1 rounded-xl border-2 border-emerald-700 bg-white px-4 py-2 text-lg font-semibold text-emerald-800", reply.label);
      b.type = "button";
      b.addEventListener("click", function () {
        sendMessage(reply.send, false);
      });
      el.quick.appendChild(b);
    });
    el.quick.hidden = !el.quick.childElementCount;
  }

  function renderAll() {
    var t = L.texts(session.lang);
    document.documentElement.lang = session.lang === "en" ? "en" : "hi";
    document.getElementById("title").textContent = t.title;
    document.getElementById("subtitle").textContent = t.subtitle;
    document.getElementById("disclaimer").textContent = t.disclaimer;
    el.input.placeholder = t.placeholder;
    el.send.textContent = t.send;
    el.mic.setAttribute("aria-label", t.mic);
    el.newChat.textContent = t.newChat;
    el.langHi.setAttribute("aria-pressed", String(session.lang !== "en"));
    el.langEn.setAttribute("aria-pressed", String(session.lang === "en"));
    el.log.textContent = "";
    if (!session.messages.length) renderWelcome();
    session.messages.forEach(renderMessage);
    renderQuickReplies();
    scrollToEnd();
  }

  function setStatus(text) {
    el.status.textContent = text || "";
    el.status.hidden = !text;
  }

  function scrollToEnd() {
    el.log.scrollTop = el.log.scrollHeight;
  }

  /* ---------- talking to the API ---------- */

  function addMessage(message) {
    if (!session.messages.length) el.log.textContent = ""; // remove the welcome box
    session.messages.push(message);
    renderMessage(message);
    saveSession();
    scrollToEnd();
  }

  function setBusy(value) {
    busy = value;
    el.send.disabled = value;
    el.input.disabled = value;
    setStatus(value ? L.texts(session.lang).thinking : "");
  }

  function sendMessage(text, viaVoice) {
    text = String(text || "").trim();
    if (!text || busy) return;
    lastInputWasVoice = !!viaVoice;
    stopSpeaking();
    addMessage({ role: "user", text: text });
    el.input.value = "";
    el.quick.hidden = true;
    setBusy(true);

    var body = { message: text };
    if (session.state) body.state = session.state;

    fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(function (response) {
        return response.json().then(
          function (data) {
            return { ok: response.ok, data: data };
          },
          function () {
            return { ok: false, data: { error: "ai_unavailable" } };
          }
        );
      })
      .then(function (result) {
        if (!result.ok) throw { code: result.data.error };
        session.state = result.data.state;
        addMessage({
          role: "assistant",
          text: result.data.reply,
          schemes: result.data.schemes,
          languageStyle: result.data.language_style
        });
        if (lastInputWasVoice) speak(result.data.reply, result.data.language_style);
      })
      .catch(function (err) {
        var code = err && err.code ? err.code : "network";
        addMessage({ role: "assistant", error: true, text: L.errorMessage(code, session.lang) });
      })
      .then(function () {
        setBusy(false);
        renderQuickReplies();
        el.input.focus();
      });
  }

  /* ---------- voice: speech synthesis (out) and recognition (in) ---------- */

  function stopSpeaking() {
    if (window.speechSynthesis) window.speechSynthesis.cancel();
  }

  function speak(text, languageStyle) {
    if (!window.speechSynthesis || !window.SpeechSynthesisUtterance) return;
    stopSpeaking();
    var utterance = new SpeechSynthesisUtterance(L.speakableText(text));
    utterance.lang = L.speechLang(languageStyle);
    var voice = window.speechSynthesis.getVoices().filter(function (v) {
      return v.lang && v.lang.replace("_", "-").indexOf(utterance.lang.slice(0, 2)) === 0;
    })[0];
    if (voice) utterance.voice = voice;
    utterance.rate = 0.95;
    window.speechSynthesis.speak(utterance);
  }

  var Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  var recognizer = null;

  function startListening() {
    var t = L.texts(session.lang);
    if (!Recognition) {
      setStatus(t.errors.no_voice);
      return;
    }
    if (recognizer) {
      recognizer.stop();
      return;
    }
    stopSpeaking();
    recognizer = new Recognition();
    recognizer.lang = L.recognitionLang(session.lang);
    recognizer.interimResults = true;
    recognizer.maxAlternatives = 1;
    var finalText = "";
    recognizer.onresult = function (event) {
      var interim = "";
      for (var i = event.resultIndex; i < event.results.length; i++) {
        if (event.results[i].isFinal) finalText += event.results[i][0].transcript;
        else interim += event.results[i][0].transcript;
      }
      el.input.value = (finalText + " " + interim).trim();
    };
    recognizer.onerror = function () {
      setStatus(t.errors.voice_failed);
    };
    recognizer.onend = function () {
      recognizer = null;
      el.mic.classList.remove("animate-pulse", "bg-red-600");
      if (el.status.textContent === t.listening) setStatus("");
      if (finalText.trim()) sendMessage(finalText, true);
    };
    el.mic.classList.add("animate-pulse", "bg-red-600");
    setStatus(t.listening);
    recognizer.start();
  }

  /* ---------- wiring ---------- */

  el.form.addEventListener("submit", function (event) {
    event.preventDefault();
    sendMessage(el.input.value, false);
  });
  el.mic.addEventListener("click", startListening);
  el.langHi.addEventListener("click", function () {
    session.lang = "hi";
    saveSession();
    renderAll();
  });
  el.langEn.addEventListener("click", function () {
    session.lang = "en";
    saveSession();
    renderAll();
  });
  el.newChat.addEventListener("click", function () {
    var lang = session.lang;
    stopSpeaking();
    session = emptySession();
    session.lang = lang;
    saveSession();
    renderAll();
  });
  if (!Recognition) el.mic.hidden = true; // e.g. Firefox: typing still works
  if (window.speechSynthesis) window.speechSynthesis.getVoices(); // start loading voices early

  renderAll();
})();
