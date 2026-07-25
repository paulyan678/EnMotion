window.enmotionDesktopBootError = (message) => {
  document.body.dataset.error = "true";
  document.querySelector("#status").textContent =
    message || "EnMotion 无法启动本地服务，请重新启动应用。";
};

window.enmotionDesktopBootStatus = (message) => {
  if (document.body.dataset.error === "true") return;
  const status = document.querySelector("#status");
  if (status && typeof message === "string" && message.trim()) {
    status.textContent = message;
  }
};
