/* Yojana Saathi - pure UI logic (no DOM), shared by the browser and the Node unit tests. */
(function (root) {
  "use strict";

  var TEXT = {
    hi: {
      title: "योजना साथी",
      subtitle: "सरकारी योजनाएँ खोजें",
      placeholder: "लिखें या बोलें…",
      send: "भेजें",
      mic: "बोलें",
      listening: "सुन रहा हूँ… बोलिए",
      thinking: "सोच रहा हूँ…",
      yes: "हाँ",
      no: "नहीं",
      noPension: "कोई पेंशन नहीं",
      newChat: "नई बातचीत",
      listen: "सुनें",
      welcome:
        "नमस्ते! मैं आपको सरकारी योजनाएँ ढूँढने में मदद करता हूँ। अपने बारे में बताइए, जैसे आपकी उम्र, काम और ज़मीन।",
      examples: ["मैं 35 साल का किसान हूँ, मेरे पास 1 हेक्टेयर ज़मीन है", "बुढ़ापे के लिए पेंशन योजना बताइए"],
      eligible: "आप पात्र हैं",
      possibly_eligible: "शायद पात्र: थोड़ी और जानकारी चाहिए",
      not_eligible: "पात्र नहीं",
      benefits: "लाभ",
      official: "आधिकारिक वेबसाइट",
      disclaimer:
        "यह केवल जानकारी है। आवेदन से पहले आधिकारिक वेबसाइट पर जाँच लें। हम कभी आधार नंबर, बैंक खाता या OTP नहीं माँगते।",
      errors: {
        rate_limited: "आप बहुत जल्दी-जल्दी संदेश भेज रहे हैं। एक मिनट रुककर फिर कोशिश करें।",
        ai_quota_exhausted:
          "आज की मुफ़्त AI सीमा शायद खत्म हो गई है। एक मिनट बाद फिर कोशिश करें; बार-बार हो तो कल कोशिश करें।",
        ai_unavailable: "AI सेवा अभी जवाब नहीं दे रही। एक मिनट बाद फिर कोशिश करें।",
        invalid_request: "कृपया अपना संदेश लिखें (1000 अक्षरों तक)।",
        network: "इंटरनेट कनेक्शन में दिक्कत है। फिर से कोशिश करें।",
        no_voice: "इस ब्राउज़र में बोलकर लिखने की सुविधा नहीं है। कृपया लिखकर भेजें।",
        voice_failed: "आवाज़ ठीक से सुनाई नहीं दी। फिर से कोशिश करें।"
      }
    },
    en: {
      title: "Yojana Saathi",
      subtitle: "Find government schemes",
      placeholder: "Type or speak…",
      send: "Send",
      mic: "Speak",
      listening: "Listening… please speak",
      thinking: "Thinking…",
      yes: "Yes",
      no: "No",
      noPension: "No pension",
      newChat: "New chat",
      listen: "Listen",
      welcome: "Namaste! I help you find government schemes. Tell me about yourself, like your age, work and land.",
      examples: ["I am a 35 year old farmer with 1 hectare of land", "Tell me about pension schemes for old age"],
      eligible: "You are eligible",
      possibly_eligible: "Possibly eligible: a little more information needed",
      not_eligible: "Not eligible",
      benefits: "Benefits",
      official: "Official website",
      disclaimer:
        "This is guidance only. Check the official website before applying. We never ask for your Aadhaar number, bank account or OTP.",
      errors: {
        rate_limited: "You are sending messages very quickly. Please wait a minute and try again.",
        ai_quota_exhausted:
          "The free AI limit may be used up. Try again in a minute; if it keeps happening, try again tomorrow.",
        ai_unavailable: "The AI service is not responding right now. Please try again in a minute.",
        invalid_request: "Please type a message (up to 1000 characters).",
        network: "There is a problem with the internet connection. Please try again.",
        no_voice: "Voice typing is not available in this browser. Please type your message.",
        voice_failed: "Could not hear that clearly. Please try again."
      }
    }
  };

  var STATUS_STYLE = {
    eligible: "bg-emerald-100 text-emerald-900 border-emerald-300",
    possibly_eligible: "bg-amber-100 text-amber-900 border-amber-300",
    not_eligible: "bg-slate-100 text-slate-700 border-slate-300"
  };

  var URL_PATTERN = /https?:\/\/[^\s<>"')\]]+/g;

  /** All UI strings for a language ("hi" or "en"; anything else falls back to Hindi). */
  function texts(lang) {
    return TEXT[lang] || TEXT.hi;
  }

  /** The user-facing message for an API error code (or "network"), in the UI language. */
  function errorMessage(code, lang) {
    var errors = texts(lang).errors;
    return errors[code] || errors.ai_unavailable;
  }

  /** Buttons to offer for the question just asked, from its field key. Each button has a
   * label and the exact text to send; bare yes/no is answered on the server without AI. */
  function quickReplies(field, lang) {
    var t = texts(lang);
    var hindi = lang !== "en";
    if (!field) return [];
    if (field.indexOf("exclusion:") === 0 || field === "owns_cultivable_land") {
      return [
        { label: t.yes, send: hindi ? "हाँ" : "Yes" },
        { label: t.no, send: hindi ? "नहीं" : "No" }
      ];
    }
    if (field === "monthly_pension") return [{ label: t.noPension, send: hindi ? "नहीं" : "No" }];
    return [];
  }

  /** BCP-47 language for speaking a reply, from the server's language_style. */
  function speechLang(languageStyle) {
    return languageStyle === "english" ? "en-IN" : "hi-IN";
  }

  /** BCP-47 language for voice typing, from the UI language. */
  function recognitionLang(lang) {
    return lang === "en" ? "en-IN" : "hi-IN";
  }

  /** Make a model reply plain: drop Markdown emphasis and turn "* " bullets into "• ". */
  function cleanReply(text) {
    return String(text || "")
      .replace(/\*\*(.+?)\*\*/g, "$1")
      .replace(/__(.+?)__/g, "$1")
      .replace(/^\s*[*-]\s+/gm, "• ")
      .trim();
  }

  /** Split text into plain-text and link parts, so links can be made clickable safely
   * (the caller builds elements with textContent, never innerHTML). */
  function splitLinks(text) {
    var parts = [];
    var last = 0;
    var source = String(text || "");
    source.replace(URL_PATTERN, function (url, offset) {
      var trimmed = url.replace(/[.,;:!?]+$/, "");
      if (offset > last) parts.push({ type: "text", value: source.slice(last, offset) });
      parts.push({ type: "link", value: trimmed });
      last = offset + trimmed.length;
      return url;
    });
    if (last < source.length) parts.push({ type: "text", value: source.slice(last) });
    return parts;
  }

  /** Text to read aloud: the cleaned reply without links (nobody wants a URL read out). */
  function speakableText(text) {
    return cleanReply(text).replace(URL_PATTERN, "").replace(/\s{2,}/g, " ").trim();
  }

  /** Label and colour classes for a scheme card's status badge. */
  function statusBadge(status, lang) {
    return { label: texts(lang)[status] || status, className: STATUS_STYLE[status] || STATUS_STYLE.not_eligible };
  }

  /** The scheme name to show in the UI language. */
  function schemeName(card, lang) {
    return lang === "en" ? card.name_en : card.name_hi || card.name_en;
  }

  var api = {
    texts: texts,
    errorMessage: errorMessage,
    quickReplies: quickReplies,
    speechLang: speechLang,
    recognitionLang: recognitionLang,
    cleanReply: cleanReply,
    splitLinks: splitLinks,
    speakableText: speakableText,
    statusBadge: statusBadge,
    schemeName: schemeName
  };

  if (typeof module === "object" && module.exports) module.exports = api;
  else root.YSLogic = api;
})(typeof self !== "undefined" ? self : this);
