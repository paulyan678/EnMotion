window.enmotionDesktopBootError = (message) => {
  document.body.dataset.error = "true";
  document.querySelector("#status").textContent =
    message || "EnMotion 无法启动本地服务，请重新启动应用。";
};
