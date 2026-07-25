import "./globals.css";
import EnvConfigChecker from "@/components/EnvConfigChecker";
import { Providers } from "@/components/Providers";
import Script from "next/script";

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh" className="atelier-dark" suppressHydrationWarning>
      <head>
        <title>EnMotion 工作室</title>
        <meta name="description" content="人工智能原生动态漫画创作平台" />
        {/*
          The desktop sidecar writes this same-origin file before opening the
          WebView. It must execute before application modules read API_URL.
          localNonce is a per-launch loopback capability, never a provider key.
        */}
        <Script src="/runtime-config.js" strategy="beforeInteractive" />
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var P=["atelier-dark","bridge-dark","brand-dark","atelier-light","brand-light"];var d=JSON.parse(localStorage.getItem("enmotion-settings")||"{}");var t=d.state&&d.state.theme;document.documentElement.className=P.indexOf(t)>=0?t:"atelier-dark";}catch(e){document.documentElement.className="atelier-dark";}})();`,
          }}
        />
      </head>
      <body className="font-sans bg-background text-foreground antialiased">
        <Providers>
          <EnvConfigChecker />
          {children}
        </Providers>
      </body>
    </html>
  );
}
