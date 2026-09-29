// Unit tests for frontend/public/js/logic.js. Run with: node --test "frontend/tests/*.test.js"
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const L = require("../public/js/logic.js");

test("yes/no buttons for exclusion and land questions, in the UI language", () => {
  assert.deepEqual(L.quickReplies("exclusion:income_tax_payer", "hi"), [
    { label: "हाँ", send: "हाँ" },
    { label: "नहीं", send: "नहीं" }
  ]);
  assert.deepEqual(
    L.quickReplies("owns_cultivable_land", "en").map((r) => r.send),
    ["Yes", "No"]
  );
});

test("pension question offers only 'no pension', which the server reads as 0", () => {
  assert.deepEqual(L.quickReplies("monthly_pension", "en"), [{ label: "No pension", send: "No" }]);
});

test("no buttons for number questions or when nothing was asked", () => {
  assert.deepEqual(L.quickReplies("age", "hi"), []);
  assert.deepEqual(L.quickReplies("landholding_hectares", "hi"), []);
  assert.deepEqual(L.quickReplies(null, "hi"), []);
});

test("replies are spoken in Hindi unless the user wrote English", () => {
  assert.equal(L.speechLang("hindi"), "hi-IN");
  assert.equal(L.speechLang("hinglish"), "hi-IN");
  assert.equal(L.speechLang("english"), "en-IN");
  assert.equal(L.recognitionLang("hi"), "hi-IN");
  assert.equal(L.recognitionLang("en"), "en-IN");
});

test("every API error code has a message in both languages, with a safe fallback", () => {
  for (const code of ["rate_limited", "ai_quota_exhausted", "ai_unavailable", "invalid_request", "network"]) {
    assert.ok(L.errorMessage(code, "hi").length > 10, code);
    assert.ok(L.errorMessage(code, "en").length > 10, code);
    assert.notEqual(L.errorMessage(code, "hi"), L.errorMessage(code, "en"));
  }
  assert.equal(L.errorMessage("something_new", "en"), L.errorMessage("ai_unavailable", "en"));
});

test("both languages define exactly the same text keys", () => {
  const keys = (lang) => Object.keys(L.texts(lang)).sort();
  assert.deepEqual(keys("hi"), keys("en"));
  assert.deepEqual(Object.keys(L.texts("hi").errors).sort(), Object.keys(L.texts("en").errors).sort());
  assert.equal(L.texts("xx"), L.texts("hi")); // unknown language falls back to Hindi
});

test("markdown from the model is flattened to plain text", () => {
  assert.equal(L.cleanReply("**PM-KISAN**: yes\n* first\n- second"), "PM-KISAN: yes\n• first\n• second");
  assert.equal(L.cleanReply(null), "");
});

test("links are split out, without trailing punctuation", () => {
  assert.deepEqual(L.splitLinks("See https://pmkisan.gov.in. Thanks"), [
    { type: "text", value: "See " },
    { type: "link", value: "https://pmkisan.gov.in" },
    { type: "text", value: ". Thanks" }
  ]);
  assert.deepEqual(L.splitLinks("no links here"), [{ type: "text", value: "no links here" }]);
});

test("only http(s) links become links (no javascript: URLs)", () => {
  const parts = L.splitLinks("click javascript:alert(1) now");
  assert.ok(parts.every((p) => p.type === "text"));
});

test("links are not read aloud", () => {
  assert.equal(L.speakableText("Apply at https://maandhan.in/ today."), "Apply at today.");
});

test("status badges and scheme names follow the UI language", () => {
  assert.equal(L.statusBadge("eligible", "hi").label, "आप पात्र हैं");
  assert.match(L.statusBadge("possibly_eligible", "en").className, /amber/);
  const card = { name_en: "PM-KISAN", name_hi: "पीएम-किसान" };
  assert.equal(L.schemeName(card, "hi"), "पीएम-किसान");
  assert.equal(L.schemeName(card, "en"), "PM-KISAN");
});
