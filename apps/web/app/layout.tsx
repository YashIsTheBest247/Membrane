import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { Shell } from "@/components/shell";

/*
 * Geist carries the interface. It was drawn for exactly this — dense product
 * UI on a dark ground — with a low-contrast skeleton that holds up at 12px and
 * genuinely good tabular figures, which matters on a screen that is mostly
 * numbers. Geist Mono takes every hash, timestamp and capability name.
 */

const sans = Geist({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});

const mono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Membrane — prompt-injection firewall for AI agents",
  description:
    "Content passes, instructions do not. A transparent proxy that separates " +
    "what a document says from what it asks an AI agent to do.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#08080a",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
