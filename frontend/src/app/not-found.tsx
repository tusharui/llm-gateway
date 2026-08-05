"use client";

import Link from "next/link";
import { motion } from "framer-motion";

export default function NotFound() {
  return (
    <div className="p-4 sm:p-6 md:p-8 flex flex-col items-center justify-center min-h-[60vh] text-center">
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
      >
        <h1 className="text-4xl sm:text-5xl font-bold">404</h1>
        <p className="text-zinc-500 mt-3 text-sm sm:text-base">
          Page not found. The page you are looking for does not exist.
        </p>
        <Link
          href="/"
          className="inline-block mt-6 bg-white text-black px-5 py-2.5 text-sm font-medium hover:bg-zinc-200 transition-colors"
        >
          Back to Dashboard
        </Link>
      </motion.div>
    </div>
  );
}
