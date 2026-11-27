import React, { createContext, useContext, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route, Navigate, NavLink, Outlet } from "react-router-dom";
import { LayoutDashboard, CalendarCheck, Users, Settings, LogOut, Moon, PanelLeftClose, PanelLeft, ExternalLink } from "lucide-react";
import "./index.css";
import { api, setCsrf } from "./lib/api";
import Dashboard from "./pages/Dashboard"; import Appointments from "./pages/Appointments"; import Login from "./pages/Login";

const Auth = createContext<any>(null); export const useAuth = () => useContext(Auth);
function Toasts() {
  const [t, setT] = useState<string[]>([]);
  useEffect(() => { const h = (e: any) => { setT((x) => [...x, e.detail]); setTimeout(() => setT((x) => x.slice(1)), 3200); };
    window.addEventListener("toast", h); return () => window.removeEventListener("toast", h); }, []);
  return <div className="fixed bottom-4 right-4 z-50 space-y-2">{t.map((m, i) => <div key={i} className="rounded-lg bg-navy px-4 py-2 text-sm text-white shadow-lg">{m}</div>)}</div>;
}
function Shell() {
  const { me, logout } = useAuth(); const [open, setOpen] = useState(true);
  const link = ({ isActive }: any) => `flex items-center gap-3 rounded-lg px-3 py-2 text-sm ${isActive ? "bg-teal-500 font-semibold text-teal-950" : "text-slate-300 hover:bg-slate-700"}`;
  return <div className="flex min-h-screen">
    <aside className={`${open ? "w-56" : "w-16"} flex shrink-0 flex-col gap-1 bg-navy p-3 transition-all`}>
      <div className="mb-4 flex items-center justify-between px-1 text-lg font-bold text-white">{open && "Appointly"}
        <button onClick={() => setOpen(!open)} aria-label="Toggle sidebar" className="text-slate-300">{open ? <PanelLeftClose size={18} /> : <PanelLeft size={18} />}</button></div>
      <NavLink to="/" end className={link}><LayoutDashboard size={18} />{open && "Dashboard"}</NavLink>
      <NavLink to="/appointments" className={link}><CalendarCheck size={18} />{open && "Appointments"}</NavLink>
      <a href="/legacy" className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-slate-300 hover:bg-slate-700"><Users size={18} />{open && <>Calendar, clients, services, availability, analytics, settings <ExternalLink size={12} /></>}</a>
      <div className="flex-1" />
      <button className="flex items-center gap-3 px-3 py-2 text-sm text-slate-300" onClick={() => document.documentElement.classList.toggle("dark")}><Moon size={18} />{open && "Dark mode"}</button>
      <button className="flex items-center gap-3 px-3 py-2 text-sm text-slate-300" onClick={logout}><LogOut size={18} />{open && me.name}</button>
    </aside>
    <main className="w-full max-w-6xl p-6"><Outlet /></main></div>;
}
function App() {
  const [me, setMe] = useState<any>(undefined);
  const load = () => api("/me").then((m) => { setCsrf(m.csrf); setMe(m); }).catch(() => setMe(null));
  useEffect(() => { load(); }, []);
  const logout = async () => { await api("/auth/logout", "POST", {}); setMe(null); };
  if (me === undefined) return <div className="p-8">Loading…</div>;
  return <Auth.Provider value={{ me, logout, reload: load }}><Toasts />
    <Routes>{me ? <Route element={<Shell />}><Route index element={<Dashboard />} /><Route path="appointments" element={<Appointments />} /><Route path="*" element={<Navigate to="/" />} /></Route>
      : <Route path="*" element={<Login />} />}</Routes></Auth.Provider>;
}
createRoot(document.getElementById("root")!).render(<BrowserRouter><App /></BrowserRouter>);
