"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import { useState, useEffect } from "react";
import { signOut } from "@/app/actions/auth";

export default function Sidebar({
  email,
  isAdmin,
}: {
  email: string;
  isAdmin: boolean;
}) {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [prevPathname, setPrevPathname] = useState(pathname);

  const navItems = [
    { href: "/", label: "Dashboard", icon: <DashboardIcon /> },
    { href: "/chat", label: "Chat", icon: <ChatIcon /> },
    { href: "/models", label: "Models", icon: <ModelsIcon /> },
    ...(isAdmin
      ? [{ href: "/analytics", label: "Analytics", icon: <AnalyticsIcon /> }]
      : []),
  ];

  if (prevPathname !== pathname) {
    setPrevPathname(pathname);
    setMobileOpen(false);
  }

  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth < 768);
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);

  function signOutUser() {
    signOut();
  }

  if (isMobile) {
    return (
      <>
        <button
          onClick={() => setMobileOpen(true)}
          className="fixed top-4 left-4 z-50 p-2 bg-black border border-white/20"
        >
          <HamburgerIcon />
        </button>

        <AnimatePresence>
          {mobileOpen && (
            <>
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                onClick={() => setMobileOpen(false)}
                className="fixed inset-0 bg-white/10 z-50 backdrop-blur-sm"
              />
              <motion.aside
                initial={{ x: -264 }}
                animate={{ x: 0 }}
                exit={{ x: -264 }}
                transition={{ type: "spring", stiffness: 400, damping: 30 }}
                className="fixed left-0 top-0 h-full w-64 border-r border-white/10 bg-black z-50 flex flex-col"
              >
                <SidebarContent
                  pathname={pathname}
                  navItems={navItems}
                  email={email}
                  onSignOut={signOutUser}
                  onClose={() => setMobileOpen(false)}
                />
              </motion.aside>
            </>
          )}
        </AnimatePresence>
      </>
    );
  }

  return (
    <motion.aside
      initial={{ x: -264 }}
      animate={{ x: 0 }}
      transition={{ type: "spring", stiffness: 300, damping: 30 }}
      className="fixed left-0 top-0 h-full w-64 border-r border-white/10 bg-black flex-col hidden md:flex"
    >
      <SidebarContent
        pathname={pathname}
        navItems={navItems}
        email={email}
        onSignOut={signOutUser}
      />
    </motion.aside>
  );
}

type NavItem = { href: string; label: string; icon: React.ReactNode };

function SidebarContent({
  pathname,
  navItems,
  email,
  onSignOut,
  onClose,
}: {
  pathname: string;
  navItems: NavItem[];
  email: string;
  onSignOut: () => void;
  onClose?: () => void;
}) {
  return (
    <>
      <motion.div
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.2, duration: 0.4 }}
        className="p-6 border-b border-white/10"
      >
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold tracking-tight">AI GATEWAY</h1>
            <p className="text-xs text-zinc-500 mt-1">Inference Proxy v1.0</p>
          </div>
          {onClose && (
            <button onClick={onClose} className="p-1 text-zinc-500 hover:text-white">
              <CloseIcon />
            </button>
          )}
        </div>
      </motion.div>

      <nav className="flex-1 p-4 space-y-1">
        {navItems.map((item, i) => {
          const isActive = pathname === item.href;
          return (
            <motion.div
              key={item.href}
              initial={{ opacity: 0, x: -20 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.3 + i * 0.05, duration: 0.3 }}
            >
              <Link
                href={item.href}
                className={`relative flex items-center gap-3 px-4 py-3 text-sm transition-colors overflow-hidden ${
                  isActive
                    ? "text-black"
                    : "text-zinc-400 hover:text-white hover:bg-white/5"
                }`}
              >
                {isActive && (
                  <motion.div
                    layoutId="sidebar-active"
                    className="absolute inset-0 bg-white"
                    transition={{ type: "spring", stiffness: 400, damping: 30 }}
                  />
                )}
                <span className="relative z-10 w-5 flex items-center justify-center">
                  {item.icon}
                </span>
                <span className="relative z-10">{item.label}</span>
              </Link>
            </motion.div>
          );
        })}
      </nav>

      <div className="p-4 border-t border-white/10 space-y-3">
        <div className="flex items-center gap-3 px-4 py-2 text-sm text-zinc-500">
          <span className="w-2 h-2 bg-white rounded-full animate-pulse" />
          System Online
        </div>
        <div className="px-4">
          <p className="text-xs text-zinc-600 truncate" title={email}>
            {email}
          </p>
          <button
            onClick={onSignOut}
            className="mt-2 px-3 py-1.5 border border-white/20 text-xs text-zinc-300 hover:bg-white hover:text-black transition-colors"
          >
            Sign out
          </button>
        </div>
      </div>
    </>
  );
}

function HamburgerIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="3" y1="6" x2="21" y2="6" />
      <line x1="3" y1="12" x2="21" y2="12" />
      <line x1="3" y1="18" x2="21" y2="18" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}

function DashboardIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="7" />
      <rect x="14" y="3" width="7" height="7" />
      <rect x="14" y="14" width="7" height="7" />
      <rect x="3" y="14" width="7" height="7" />
    </svg>
  );
}

function ChatIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </svg>
  );
}

function ModelsIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="12 2 2 7 12 12 22 7 12 2" />
      <polyline points="2 17 12 22 22 17" />
      <polyline points="2 12 12 17 22 12" />
    </svg>
  );
}

function AnalyticsIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="20" x2="18" y2="10" />
      <line x1="12" y1="20" x2="12" y2="4" />
      <line x1="6" y1="20" x2="6" y2="14" />
    </svg>
  );
}