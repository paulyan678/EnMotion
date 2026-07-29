import { render, type RenderOptions } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactElement, ReactNode } from "react";

import { LightboxProvider } from "@/components/shared/preview/LightboxProvider";
import { getMessages, type MessageLocale } from "@/lib/i18n";

const TEST_TIME_ZONE = "Asia/Shanghai";

type IntlRenderOptions = Omit<RenderOptions, "wrapper"> & {
  locale?: MessageLocale;
};

function IntlTestProvider({
  children,
  locale,
}: {
  children: ReactNode;
  locale: MessageLocale;
}) {
  return (
    <NextIntlClientProvider
      locale={locale}
      messages={getMessages(locale)}
      timeZone={TEST_TIME_ZONE}
    >
      <LightboxProvider>{children}</LightboxProvider>
    </NextIntlClientProvider>
  );
}

/** Render client components with the same locale data and time zone as Providers. */
export function renderWithIntl(
  ui: ReactElement,
  { locale = "zh", ...renderOptions }: IntlRenderOptions = {},
) {
  return render(ui, {
    wrapper: ({ children }) => (
      <IntlTestProvider locale={locale}>{children}</IntlTestProvider>
    ),
    ...renderOptions,
  });
}
