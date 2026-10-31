/* Голосовой слой (voice): STT и TTS средствами браузера (Web Speech API), без серверной части.
   Чтобы заменить на Whisper, достаточно реализовать те же два метода: createRecognizer и speak. */
const Voice = (() => {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const supported = !!SR;

  /* handlers: onInterim(text), onFinal(text), onState(listening:boolean), onError(code) */
  function createRecognizer(handlers) {
    if (!SR) return null;
    const rec = new SR();
    rec.lang = 'ru-RU';
    rec.interimResults = true;
    rec.continuous = false;
    let listening = false, finalText = '';
    rec.onresult = e => {
      let all = '';
      finalText = '';
      for (const r of e.results) {
        all += r[0].transcript;
        if (r.isFinal) finalText += r[0].transcript;
      }
      handlers.onInterim && handlers.onInterim(all);
    };
    rec.onend = () => {
      listening = false;
      handlers.onState && handlers.onState(false);
      if (finalText) handlers.onFinal && handlers.onFinal(finalText);
    };
    rec.onerror = e => {
      listening = false;
      handlers.onState && handlers.onState(false);
      handlers.onError && handlers.onError(e.error);
    };
    return {
      toggle() {
        if (listening) { rec.stop(); return; }
        speak.cancel();
        finalText = '';
        listening = true;
        handlers.onState && handlers.onState(true);
        rec.start();
      }
    };
  }

  function speak(text) {
    if (!window.speechSynthesis) return;
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = 'ru-RU';
    speechSynthesis.speak(u);
  }
  speak.cancel = () => window.speechSynthesis && speechSynthesis.cancel();

  return { supported, createRecognizer, speak };
})();
