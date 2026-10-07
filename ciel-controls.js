"use strict";

(() => {
  const messageInput = document.querySelector("#messageInput");
  const chatForm = document.querySelector("#chatForm");
  const newChatButton = document.querySelector("#newChatButton");
  const settingsModal = document.querySelector("#settingsModal");

  if (!messageInput || !chatForm || !newChatButton || !settingsModal) return;

  function isTypingTarget(target) {
    return target instanceof HTMLElement && (
      target.isContentEditable
      || target.matches("input, textarea, select")
    );
  }

  document.addEventListener("keydown", event => {
    const modifier = event.ctrlKey || event.metaKey;
    const key = event.key.toLowerCase();

    if (event.key === "Escape") {
      if (settingsModal.classList.contains("open")) {
        settingsModal.classList.remove("open");
      } else if ("speechSynthesis" in window) {
        window.speechSynthesis.cancel();
      }
      return;
    }

    if (!modifier || event.altKey) return;

    if (key === "k") {
      event.preventDefault();
      messageInput.focus();
      messageInput.select();
      return;
    }

    if (key === "n" && event.shiftKey) {
      event.preventDefault();
      newChatButton.click();
      return;
    }

    if (key === "enter" && !event.shiftKey && !isTypingTarget(event.target)) {
      event.preventDefault();
      chatForm.requestSubmit();
    }
  });
})();
