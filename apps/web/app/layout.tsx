import type { Metadata } from "next";
import { IBM_Plex_Mono, Plus_Jakarta_Sans } from "next/font/google";
import "./globals.css";
import { Shell } from "@/components/shell";

/*
 * Plus Jakarta Sans carries the interface: a geometric grotesque with tight
 * apertures and genuinely good lining figures, which matters on a screen that
 * is mostly numbers. IBM Plex Mono takes every hash, timestamp and capability
 * name — enough character to make a column of digests pleasant to scan.
 */

const sans = Plus_Jakarta_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-sans",
  display: "swap",
});

const mono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Membrane · prompt-injection firewall",
  description:
    "Live attack feed, held-action queue, session replay and InjectBench " +
    "results for the Membrane proxy.",
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
