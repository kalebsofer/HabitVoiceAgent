import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Voice Agent",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
        <style>{`
          @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.3; }
          }
        `}</style>
      </head>
      <body>{children}</body>
    </html>
  );
}
