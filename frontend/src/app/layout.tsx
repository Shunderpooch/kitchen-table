import { MEMBER_A, MEMBER_B } from "~/lib/household";
import "~/styles/globals.css";

import { type Metadata } from "next";
import { Inter } from "next/font/google";

import { TRPCReactProvider } from "~/trpc/react";

export const metadata: Metadata = {
  title: `Expenses | ${MEMBER_A} & ${MEMBER_B}`,
  description: "Household expense tracking and analysis",
  icons: [{ rel: "icon", url: "/favicon.ico" }],
};

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
});

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${inter.variable} dark`}>
      <body className="bg-zinc-950 text-zinc-100 antialiased">
        <TRPCReactProvider>{children}</TRPCReactProvider>
      </body>
    </html>
  );
}
