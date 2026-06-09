import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  CirclePlus,
  Edit3,
  ExternalLink,
  Monitor,
  Power,
  RefreshCw,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

type ViewName = "dashboard" | "browsers" | "tabs" | "viewers";

type Viewport = {
  width: number;
  height: number;
};

type TabInfo = {
  tab_id: number;
  browser_id: number;
  room?: string;
  url?: string | null;
  viewport?: Viewport | null;
  viewer_count?: number;
  status?: string;
  persistent?: boolean;
  created_by?: string;
  domain_locked?: boolean;
  allowed_hostname?: string | null;
  workbench_id?: number | null;
  workbench_name?: string | null;
  workbench_tab_id?: number;
  available?: boolean;
};

type BrowserInfo = {
  browser_id: number;
  name?: string;
  proxy?: string | null;
  chrome_version?: string | null;
  status: string;
  tab_count: number;
  total_viewers: number;
  tabs?: TabInfo[];
};

type DashboardData = {
  total_browsers: number;
  running_browsers: number;
  total_tabs: number;
  total_viewers: number;
  browsers: BrowserInfo[];
};

type Workbench = {
  id: number;
  name: string;
  created_at?: string;
  active_browser_id?: number | null;
  active_tab_id?: number | null;
  active_room?: string | null;
  recording_enabled?: boolean;
  recording_room?: string | null;
  recording_file?: string | null;
  tabs: TabInfo[];
  active_tabs: TabInfo[];
  tab_count: number;
  active_tab_count: number;
  url: string;
};

type ViewerInfo = {
  id: string;
  browser_id: number;
  tab_id: number;
  client_host: string;
  client_port: number;
  current_url?: string | null;
};

type ToastType = "success" | "error" | "info";

type ToastState = {
  message: string;
  type: ToastType;
} | null;

type ApiError = Error & { status?: number };

const DEFAULT_DASHBOARD: DashboardData = {
  total_browsers: 0,
  running_browsers: 0,
  total_tabs: 0,
  total_viewers: 0,
  browsers: [],
};

async function api<T>(method: string, path: string, body?: unknown): Promise<T> {
  const opts: RequestInit = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.detail || data.error || data.message || "API error") as ApiError;
    err.status = res.status;
    throw err;
  }
  return data as T;
}

function tabKey(browserId: number, tabId: number) {
  return `${browserId}:${tabId}`;
}

function parseTabKey(key: string) {
  const [browserId, tabId] = key.split(":").map(Number);
  return { browser_id: browserId, tab_id: tabId };
}

function truncate(value: string | null | undefined, max = 68) {
  const text = value || "";
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

function statusVariant(status?: string) {
  if (!status) return "muted" as const;
  if (["idle", "active", "completed"].includes(status)) return "success" as const;
  if (["launching", "closing", "busy", "preheating", "processing"].includes(status)) return "warning" as const;
  if (["error", "failed"].includes(status)) return "destructive" as const;
  return "muted" as const;
}

function StatusBadge({ status }: { status?: string }) {
  return <Badge variant={statusVariant(status)}>{status || "unknown"}</Badge>;
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="grid gap-1.5 text-sm">
      <span className="font-medium text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}

function EmptyRow({ colSpan, children }: { colSpan: number; children: React.ReactNode }) {
  return (
    <TableRow>
      <TableCell colSpan={colSpan} className="h-24 text-center text-muted-foreground">
        {children}
      </TableCell>
    </TableRow>
  );
}

function StatCard({ label, value, tone }: { label: string; value: number | string; tone: string }) {
  return (
    <Card className={cn("border-l-4", tone)}>
      <CardContent className="p-5">
        <div className="text-3xl font-semibold">{value}</div>
        <div className="mt-1 text-sm text-muted-foreground">{label}</div>
      </CardContent>
    </Card>
  );
}

export default function App() {
  const [view, setView] = useState<ViewName>("dashboard");
  const [dashboard, setDashboard] = useState<DashboardData>(DEFAULT_DASHBOARD);
  const [workbenches, setWorkbenches] = useState<Workbench[]>([]);
  const [viewers, setViewers] = useState<ViewerInfo[]>([]);
  const [selectedTabKeys, setSelectedTabKeys] = useState<Set<string>>(new Set());
  const [toast, setToast] = useState<ToastState>(null);
  const [loading, setLoading] = useState(false);
  const [codec, setCodecState] = useState("h264");
  const [chromeVersions, setChromeVersions] = useState<string[]>([]);
  const [recordingWorkbenchIds, setRecordingWorkbenchIds] = useState<Set<number>>(new Set());

  const [createBrowserOpen, setCreateBrowserOpen] = useState(false);
  const [createTabFor, setCreateTabFor] = useState<BrowserInfo | null>(null);
  const [editBrowser, setEditBrowser] = useState<BrowserInfo | null>(null);
  const [createWorkbenchOpen, setCreateWorkbenchOpen] = useState(false);
  const [addTabsWorkbench, setAddTabsWorkbench] = useState<Workbench | null>(null);
  const [removeTabsWorkbench, setRemoveTabsWorkbench] = useState<Workbench | null>(null);
  const [deleteWorkbench, setDeleteWorkbench] = useState<Workbench | null>(null);
  const [removeTabKeys, setRemoveTabKeys] = useState<Set<string>>(new Set());

  const [browserForm, setBrowserForm] = useState({
    name: "",
    proxy: "",
    user_data_dir: "",
    chrome_version: "",
    auto_start: true,
  });
  const [tabForm, setTabForm] = useState({
    url: "https://www.bing.com",
    viewport_width: 1280,
    viewport_height: 720,
  });
  const [workbenchName, setWorkbenchName] = useState("");

  const allTabs = useMemo(() => dashboard.browsers.flatMap((browser) => browser.tabs || []), [dashboard.browsers]);

  const workbenchByTab = useMemo(() => {
    const map = new Map<string, Workbench>();
    workbenches.forEach((workbench) => {
      workbench.tabs.forEach((tab) => map.set(tabKey(tab.browser_id, tab.tab_id), workbench));
    });
    return map;
  }, [workbenches]);

  const selectedTabs = useMemo(() => [...selectedTabKeys].map(parseTabKey), [selectedTabKeys]);

  const showToast = useCallback((message: string, type: ToastType = "info") => {
    setToast({ message, type });
    window.setTimeout(() => setToast(null), 3000);
  }, []);

  const refreshViewers = useCallback(async () => {
    const data = await api<{ viewers: ViewerInfo[] }>("GET", "/api/admin/viewers");
    setViewers(data.viewers || []);
  }, []);

  const refreshData = useCallback(async () => {
    setLoading(true);
    try {
      const [dashboardData, workbenchData] = await Promise.all([
        api<DashboardData>("GET", "/api/admin/dashboard"),
        api<{ workbenches: Workbench[] }>("GET", "/api/admin/workbenches"),
      ]);
      setDashboard(dashboardData);
      setWorkbenches(workbenchData.workbenches || []);
      setSelectedTabKeys((current) => {
        const liveEligible = new Set<string>();
        const assigned = new Set<string>();
        (workbenchData.workbenches || []).forEach((workbench) => {
          workbench.tabs.forEach((tab) => assigned.add(tabKey(tab.browser_id, tab.tab_id)));
        });
        dashboardData.browsers.forEach((browser) => {
          (browser.tabs || []).forEach((tab) => {
            const key = tabKey(tab.browser_id, tab.tab_id);
            if (tab.persistent && tab.created_by === "admin" && !tab.workbench_id && !assigned.has(key)) {
              liveEligible.add(key);
            }
          });
        });
        return new Set([...current].filter((key) => liveEligible.has(key)));
      });
      if (view === "viewers") await refreshViewers();
    } catch (err) {
      showToast((err as Error).message, "error");
    } finally {
      setLoading(false);
    }
  }, [refreshViewers, showToast, view]);

  const loadCodec = useCallback(async () => {
    try {
      const data = await api<{ codec: string }>("GET", "/api/codec");
      setCodecState(data.codec);
    } catch {
      // optional endpoint
    }
  }, []);

  useEffect(() => {
    refreshData();
    loadCodec();
    api<{ available_versions: string[] }>("GET", "/api/admin/chrome-versions")
      .then((data) => setChromeVersions(data.available_versions || []))
      .catch(() => undefined);
    const timer = window.setInterval(() => refreshData(), 5000);
    return () => window.clearInterval(timer);
  }, [loadCodec, refreshData]);

  async function runAction(fn: () => Promise<void>, success?: string) {
    try {
      await fn();
      if (success) showToast(success, "success");
      await refreshData();
    } catch (err) {
      showToast((err as Error).message, "error");
    }
  }

  async function setCodec(codecValue: string) {
    await runAction(async () => {
      await api("PUT", "/api/codec", { codec: codecValue });
      setCodecState(codecValue);
    }, `Codec set to ${codecValue.toUpperCase()}`);
  }

  async function toggleWorkbenchRecording(workbench: Workbench) {
    setRecordingWorkbenchIds((current) => new Set(current).add(workbench.id));
    try {
      const next = !workbench.recording_enabled;
      await api("POST", `/api/admin/workbenches/${workbench.id}/recording`, { recording: next });
      showToast(next ? "Workbench recording started" : "Workbench recording stopped", "success");
      await refreshData();
    } catch (err) {
      showToast((err as Error).message, "error");
    } finally {
      setRecordingWorkbenchIds((current) => {
        const next = new Set(current);
        next.delete(workbench.id);
        return next;
      });
    }
  }

  function toggleTabSelection(tab: TabInfo, checked: boolean) {
    const key = tabKey(tab.browser_id, tab.tab_id);
    setSelectedTabKeys((current) => {
      const next = new Set(current);
      if (checked) next.add(key);
      else next.delete(key);
      return next;
    });
  }

  function clearSelection() {
    setSelectedTabKeys(new Set());
  }

  const eligibleTabs = allTabs.filter((tab) => {
    const key = tabKey(tab.browser_id, tab.tab_id);
    return tab.persistent && tab.created_by === "admin" && !tab.workbench_id && !workbenchByTab.has(key);
  });

  async function submitCreateBrowser() {
    await runAction(async () => {
      await api("POST", "/api/admin/browsers", {
        name: browserForm.name.trim(),
        proxy: browserForm.proxy.trim() || undefined,
        user_data_dir: browserForm.user_data_dir.trim() || undefined,
        chrome_version: browserForm.chrome_version || undefined,
        auto_start: browserForm.auto_start,
      });
      setCreateBrowserOpen(false);
      setBrowserForm({ name: "", proxy: "", user_data_dir: "", chrome_version: "", auto_start: true });
    }, "Browser created");
  }

  async function submitEditBrowser() {
    if (!editBrowser) return;
    await runAction(async () => {
      await api("PUT", `/api/admin/browsers/${editBrowser.browser_id}`, {
        name: browserForm.name.trim(),
        proxy: browserForm.proxy.trim(),
        chrome_version: browserForm.chrome_version || undefined,
      });
      setEditBrowser(null);
    }, "Browser updated");
  }

  async function submitCreateTab() {
    if (!createTabFor) return;
    await runAction(async () => {
      await api("POST", `/api/admin/browsers/${createTabFor.browser_id}/tabs`, tabForm);
      setCreateTabFor(null);
    }, "Tab created");
  }

  async function submitCreateWorkbench() {
    if (!selectedTabs.length) return;
    await runAction(async () => {
      await api("POST", "/api/admin/workbenches", {
        name: workbenchName.trim(),
        tabs: selectedTabs,
      });
      setCreateWorkbenchOpen(false);
      setWorkbenchName("");
      clearSelection();
    }, "Workbench created");
  }

  async function submitAddTabs() {
    if (!addTabsWorkbench || !selectedTabs.length) return;
    await runAction(async () => {
      await api("POST", `/api/admin/workbenches/${addTabsWorkbench.id}/tabs`, { tabs: selectedTabs });
      setAddTabsWorkbench(null);
      clearSelection();
    }, "Tabs added");
  }

  async function submitRemoveTabs() {
    if (!removeTabsWorkbench || !removeTabKeys.size) return;
    await runAction(async () => {
      await api("POST", `/api/admin/workbenches/${removeTabsWorkbench.id}/tabs/remove`, {
        tabs: [...removeTabKeys].map(parseTabKey),
      });
      setRemoveTabsWorkbench(null);
      setRemoveTabKeys(new Set());
    }, "Tabs removed");
  }

  async function submitDeleteWorkbench() {
    if (!deleteWorkbench) return;
    await runAction(async () => {
      await api("DELETE", `/api/admin/workbenches/${deleteWorkbench.id}`);
      setDeleteWorkbench(null);
    }, "Workbench deleted");
  }

  function openEditBrowser(browser: BrowserInfo) {
    setEditBrowser(browser);
    setBrowserForm({
      name: browser.name || "",
      proxy: browser.proxy || "",
      user_data_dir: "",
      chrome_version: browser.chrome_version || "",
      auto_start: true,
    });
  }

  function openCreateTab(browser: BrowserInfo) {
    setCreateTabFor(browser);
    setTabForm({ url: "https://www.bing.com", viewport_width: 1280, viewport_height: 720 });
  }

  function openRemoveTabs(workbench: Workbench) {
    setRemoveTabsWorkbench(workbench);
    setRemoveTabKeys(new Set());
  }

  function removeSingleWorkbenchTab(workbench: Workbench, tab: TabInfo) {
    return runAction(
      () =>
        api("POST", `/api/admin/workbenches/${workbench.id}/tabs/remove`, {
          tabs: [{ browser_id: tab.browser_id, tab_id: tab.tab_id }],
        }),
      "Tab released",
    );
  }

  const removeAllSelected = removeTabsWorkbench ? removeTabKeys.size >= removeTabsWorkbench.tabs.length : false;

  return (
    <div className="min-h-screen">
      <header className="border-b bg-card">
        <div className="mx-auto flex max-w-[1500px] flex-col gap-4 px-5 py-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <div className="flex items-center gap-2">
              <Monitor className="h-5 w-5 text-primary" />
              <h1 className="text-xl font-semibold">TabShare Admin</h1>
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              {dashboard.running_browsers} running / {dashboard.total_browsers} browsers
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {["h264", "vp8", "vp9", "av1"].map((item) => (
              <Button
                key={item}
                variant={codec === item ? "default" : "outline"}
                size="sm"
                onClick={() => setCodec(item)}
              >
                {item.toUpperCase()}
              </Button>
            ))}
            <Button variant="outline" size="sm" onClick={refreshData} disabled={loading}>
              <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
              Refresh
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1500px] px-5 py-5">
        <Tabs value={view} onValueChange={(value) => setView(value as ViewName)}>
          <TabsList>
            <TabsTrigger value="dashboard">Dashboard</TabsTrigger>
            <TabsTrigger value="browsers">Browsers</TabsTrigger>
            <TabsTrigger value="tabs">Tabs</TabsTrigger>
            <TabsTrigger value="viewers" onClick={() => refreshViewers().catch(() => undefined)}>
              Viewers
            </TabsTrigger>
          </TabsList>

          <TabsContent value="dashboard">
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <StatCard label="Browsers" value={dashboard.total_browsers} tone="border-blue-500" />
              <StatCard label="Running" value={dashboard.running_browsers} tone="border-emerald-500" />
              <StatCard label="Tabs" value={dashboard.total_tabs} tone="border-cyan-500" />
              <StatCard label="Viewers" value={dashboard.total_viewers} tone="border-amber-500" />
            </div>
            <div className="mt-4 grid gap-4">
              <BrowserTable
                browsers={dashboard.browsers}
                compact
                workbenchByTab={workbenchByTab}
                selected={selectedTabKeys}
                onToggleTabSelection={toggleTabSelection}
                onCreateTab={openCreateTab}
                onEdit={openEditBrowser}
                onLaunch={(id) => runAction(() => api("POST", `/api/admin/browsers/${id}/launch`), "Browser launching")}
                onClose={(id) => runAction(() => api("POST", `/api/admin/browsers/${id}/close`), "Browser closed")}
                onDelete={(id) => runAction(() => api("DELETE", `/api/admin/browsers/${id}`), "Browser deleted")}
                onCloseTab={(tab) =>
                  runAction(
                    () => api("DELETE", `/api/admin/browsers/${tab.browser_id}/tabs/${tab.tab_id}`),
                    "Tab closed",
                  )
                }
              />
              <WorkbenchTable
                workbenches={workbenches}
                selectedCount={selectedTabKeys.size}
                onCreateWorkbench={() => setCreateWorkbenchOpen(true)}
                onAddTabs={setAddTabsWorkbench}
                onRemoveTabs={openRemoveTabs}
                onRemoveTab={removeSingleWorkbenchTab}
                onToggleRecording={toggleWorkbenchRecording}
                recordingBusyIds={recordingWorkbenchIds}
                onDelete={setDeleteWorkbench}
              />
            </div>
          </TabsContent>

          <TabsContent value="browsers">
            <Card>
              <CardHeader className="flex-row flex-wrap items-center justify-between gap-3">
                <div>
                  <CardTitle>Browsers</CardTitle>
                  <CardDescription>Persistent browser profiles</CardDescription>
                </div>
                <Button onClick={() => setCreateBrowserOpen(true)}>
                  <CirclePlus className="h-4 w-4" />
                  Browser
                </Button>
              </CardHeader>
              <CardContent>
                <BrowserTable
                  browsers={dashboard.browsers}
                  workbenchByTab={workbenchByTab}
                  selected={selectedTabKeys}
                  onToggleTabSelection={toggleTabSelection}
                  onCreateTab={openCreateTab}
                  onEdit={openEditBrowser}
                  onLaunch={(id) => runAction(() => api("POST", `/api/admin/browsers/${id}/launch`), "Browser launching")}
                  onClose={(id) => runAction(() => api("POST", `/api/admin/browsers/${id}/close`), "Browser closed")}
                  onDelete={(id) => runAction(() => api("DELETE", `/api/admin/browsers/${id}`), "Browser deleted")}
                  onCloseTab={(tab) =>
                    runAction(
                      () => api("DELETE", `/api/admin/browsers/${tab.browser_id}/tabs/${tab.tab_id}`),
                      "Tab closed",
                    )
                  }
                />
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="tabs">
            <div className="grid gap-4">
              <Card>
                <CardHeader className="flex-row flex-wrap items-center justify-between gap-3">
                  <div>
                    <CardTitle>Tabs</CardTitle>
                    <CardDescription>
                      {eligibleTabs.length} idle / {selectedTabKeys.size} selected
                    </CardDescription>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => setSelectedTabKeys(new Set(eligibleTabs.map((tab) => tabKey(tab.browser_id, tab.tab_id))))}
                      disabled={!eligibleTabs.length}
                    >
                      Select all
                    </Button>
                    <Button variant="outline" size="sm" onClick={clearSelection}>
                      Clear
                    </Button>
                    <Button size="sm" disabled={!selectedTabKeys.size} onClick={() => setCreateWorkbenchOpen(true)}>
                      <CirclePlus className="h-4 w-4" />
                      Workbench
                    </Button>
                  </div>
                </CardHeader>
                <CardContent>
                  <TabsTable
                    tabs={eligibleTabs}
                    selected={selectedTabKeys}
                    onToggle={toggleTabSelection}
                    onDelete={(tab) =>
                      runAction(
                        () => api("DELETE", `/api/admin/browsers/${tab.browser_id}/tabs/${tab.tab_id}`),
                        "Tab closed",
                      )
                    }
                  />
                </CardContent>
              </Card>
              <WorkbenchTable
                workbenches={workbenches}
                selectedCount={selectedTabKeys.size}
                onAddTabs={setAddTabsWorkbench}
                onRemoveTabs={openRemoveTabs}
                onRemoveTab={removeSingleWorkbenchTab}
                onToggleRecording={toggleWorkbenchRecording}
                recordingBusyIds={recordingWorkbenchIds}
                onDelete={setDeleteWorkbench}
              />
            </div>
          </TabsContent>

          <TabsContent value="viewers">
            <Card>
              <CardHeader className="flex-row flex-wrap items-center justify-between gap-3">
                <div>
                  <CardTitle>Viewers</CardTitle>
                  <CardDescription>Active websocket clients</CardDescription>
                </div>
                <Button variant="outline" size="sm" onClick={() => refreshViewers().catch((err) => showToast(err.message, "error"))}>
                  <RefreshCw className="h-4 w-4" />
                  Refresh
                </Button>
              </CardHeader>
              <CardContent>
                <ViewersTable
                  viewers={viewers}
                  onKick={(viewer, viewerIndex) =>
                    runAction(
                      () => api("DELETE", `/api/admin/viewers/${viewer.browser_id}/${viewer.tab_id}/${viewerIndex}`),
                      "Viewer kicked",
                    ).then(() => refreshViewers())
                  }
                />
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </main>

      <CreateBrowserDialog
        open={createBrowserOpen}
        onOpenChange={setCreateBrowserOpen}
        form={browserForm}
        setForm={setBrowserForm}
        chromeVersions={chromeVersions}
        onSubmit={submitCreateBrowser}
      />
      <EditBrowserDialog
        browser={editBrowser}
        onOpenChange={(open) => !open && setEditBrowser(null)}
        form={browserForm}
        setForm={setBrowserForm}
        chromeVersions={chromeVersions}
        onSubmit={submitEditBrowser}
      />
      <CreateTabDialog
        browser={createTabFor}
        onOpenChange={(open) => !open && setCreateTabFor(null)}
        form={tabForm}
        setForm={setTabForm}
        onSubmit={submitCreateTab}
      />
      <CreateWorkbenchDialog
        open={createWorkbenchOpen}
        onOpenChange={setCreateWorkbenchOpen}
        selectedCount={selectedTabKeys.size}
        name={workbenchName}
        setName={setWorkbenchName}
        onSubmit={submitCreateWorkbench}
      />
      <AddTabsDialog
        workbench={addTabsWorkbench}
        selectedTabs={selectedTabs}
        onOpenChange={(open) => !open && setAddTabsWorkbench(null)}
        onSubmit={submitAddTabs}
      />
      <RemoveTabsDialog
        workbench={removeTabsWorkbench}
        selected={removeTabKeys}
        setSelected={setRemoveTabKeys}
        removesAll={removeAllSelected}
        onOpenChange={(open) => !open && setRemoveTabsWorkbench(null)}
        onSubmit={submitRemoveTabs}
      />
      <DeleteWorkbenchDialog
        workbench={deleteWorkbench}
        onOpenChange={(open) => !open && setDeleteWorkbench(null)}
        onSubmit={submitDeleteWorkbench}
      />

      {toast && (
        <div
          className={cn(
            "toast rounded-md border bg-card px-4 py-3 text-sm shadow-lg",
            toast.type === "success" && "border-emerald-200 text-emerald-700",
            toast.type === "error" && "border-red-200 text-red-700",
          )}
        >
          {toast.message}
        </div>
      )}
    </div>
  );
}

function BrowserTable({
  browsers,
  compact = false,
  workbenchByTab,
  selected,
  onToggleTabSelection,
  onCreateTab,
  onEdit,
  onLaunch,
  onClose,
  onDelete,
  onCloseTab,
}: {
  browsers: BrowserInfo[];
  compact?: boolean;
  workbenchByTab: Map<string, Workbench>;
  selected: Set<string>;
  onToggleTabSelection: (tab: TabInfo, checked: boolean) => void;
  onCreateTab: (browser: BrowserInfo) => void;
  onEdit: (browser: BrowserInfo) => void;
  onLaunch: (id: number) => void;
  onClose: (id: number) => void;
  onDelete: (id: number) => void;
  onCloseTab: (tab: TabInfo) => void;
}) {
  const [expandedBrowserIds, setExpandedBrowserIds] = useState<Set<number>>(new Set());

  function toggleExpanded(browserId: number) {
    setExpandedBrowserIds((current) => {
      const next = new Set(current);
      if (next.has(browserId)) next.delete(browserId);
      else next.add(browserId);
      return next;
    });
  }

  return (
    <Card className={compact ? "" : "border-0 shadow-none"}>
      {compact && (
        <CardHeader>
          <CardTitle>Browsers</CardTitle>
          <CardDescription>{browsers.length} persistent profiles</CardDescription>
        </CardHeader>
      )}
      <CardContent className={compact ? "" : "p-0"}>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Details</TableHead>
              <TableHead>ID</TableHead>
              <TableHead>Name</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Tabs</TableHead>
              <TableHead>Viewers</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {!browsers.length && <EmptyRow colSpan={7}>No browsers configured</EmptyRow>}
            {browsers.map((browser) => {
              const expanded = expandedBrowserIds.has(browser.browser_id);
              return (
                <Fragment key={browser.browser_id}>
                  <TableRow>
                    <TableCell>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => toggleExpanded(browser.browser_id)}
                        aria-label={expanded ? "Collapse browser tabs" : "Expand browser tabs"}
                      >
                        {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                        Tabs
                      </Button>
                    </TableCell>
                    <TableCell className="font-medium">{browser.browser_id}</TableCell>
                    <TableCell>
                      <div className="font-medium">{browser.name || "unnamed"}</div>
                      <div className="text-xs text-muted-foreground">{browser.proxy || "no proxy"}</div>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={browser.status} />
                    </TableCell>
                    <TableCell>{browser.tab_count}</TableCell>
                    <TableCell>{browser.total_viewers}</TableCell>
                    <TableCell>
                      <div className="flex justify-end gap-2">
                        {browser.status === "idle" ? (
                          <>
                            <Button size="sm" onClick={() => onCreateTab(browser)}>
                              <CirclePlus className="h-4 w-4" />
                              Tab
                            </Button>
                            <Button size="sm" variant="outline" onClick={() => onEdit(browser)}>
                              <Edit3 className="h-4 w-4" />
                              Edit
                            </Button>
                            <Button size="sm" variant="warning" onClick={() => onClose(browser.browser_id)}>
                              <Square className="h-4 w-4" />
                              Stop
                            </Button>
                          </>
                        ) : browser.status === "closed" || browser.status === "error" ? (
                          <>
                            <Button size="sm" onClick={() => onLaunch(browser.browser_id)}>
                              <Power className="h-4 w-4" />
                              Launch
                            </Button>
                            <Button size="sm" variant="outline" onClick={() => onEdit(browser)}>
                              <Edit3 className="h-4 w-4" />
                              Edit
                            </Button>
                            <Button size="sm" variant="destructive" onClick={() => onDelete(browser.browser_id)}>
                              <Trash2 className="h-4 w-4" />
                              Delete
                            </Button>
                          </>
                        ) : (
                          <Badge variant="warning">{browser.status}</Badge>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                  {expanded && (
                    <TableRow>
                      <TableCell colSpan={7} className="bg-muted/30 p-0">
                        <BrowserTabsPanel
                          browser={browser}
                          workbenchByTab={workbenchByTab}
                          selected={selected}
                          onToggleTabSelection={onToggleTabSelection}
                          onCloseTab={onCloseTab}
                        />
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              );
            })}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function BrowserTabsPanel({
  browser,
  workbenchByTab,
  selected,
  onToggleTabSelection,
  onCloseTab,
}: {
  browser: BrowserInfo;
  workbenchByTab: Map<string, Workbench>;
  selected: Set<string>;
  onToggleTabSelection: (tab: TabInfo, checked: boolean) => void;
  onCloseTab: (tab: TabInfo) => void;
}) {
  const tabs = browser.tabs || [];

  return (
    <div className="px-4 py-3">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-10" />
            <TableHead>Tab</TableHead>
            <TableHead>URL</TableHead>
            <TableHead>Viewport</TableHead>
            <TableHead>Workbench</TableHead>
            <TableHead>Viewers</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {!tabs.length && <EmptyRow colSpan={7}>No tabs in this browser</EmptyRow>}
          {tabs.map((tab) => {
            const key = tabKey(tab.browser_id, tab.tab_id);
            const persistedWorkbench = workbenchByTab.get(key);
            const assigned = Boolean(tab.workbench_id || persistedWorkbench);
            const selectable = Boolean(tab.persistent && tab.created_by === "admin" && !assigned);
            const viewport = tab.viewport || { width: 0, height: 0 };
            return (
              <TableRow key={key}>
                <TableCell>
                  <input
                    type="checkbox"
                    className="h-4 w-4 rounded border-input"
                    checked={selected.has(key)}
                    disabled={!selectable}
                    onChange={(event) => onToggleTabSelection(tab, event.target.checked)}
                  />
                </TableCell>
                <TableCell className="font-medium">
                  {tab.browser_id}/{tab.tab_id}
                </TableCell>
                <TableCell>
                  <div className="max-w-[520px] truncate" title={tab.url || ""}>
                    {truncate(tab.url || "about:blank", 92)}
                  </div>
                </TableCell>
                <TableCell>{viewport.width && viewport.height ? `${viewport.width}x${viewport.height}` : "-"}</TableCell>
                <TableCell>
                  {assigned ? (
                    <Badge variant="secondary">{tab.workbench_name || persistedWorkbench?.name || `#${tab.workbench_id}`}</Badge>
                  ) : selectable ? (
                    <Badge variant="muted">idle</Badge>
                  ) : (
                    <Badge variant="outline">temporary</Badge>
                  )}
                </TableCell>
                <TableCell>{tab.viewer_count || 0}</TableCell>
                <TableCell>
                  <div className="flex justify-end gap-2">
                    <Button size="sm" variant="outline" asChild>
                      <a href={`/viewer/${tab.browser_id}/${tab.tab_id}`} target="_blank" rel="noreferrer">
                        <ExternalLink className="h-4 w-4" />
                        View
                      </a>
                    </Button>
                    <Button
                      size="sm"
                      variant="destructive"
                      disabled={assigned}
                      onClick={() => onCloseTab(tab)}
                      title={assigned ? "Release the tab from its workbench before closing it" : undefined}
                    >
                      <Trash2 className="h-4 w-4" />
                      Close
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

function TabsTable({
  tabs,
  selected,
  onToggle,
  onDelete,
}: {
  tabs: TabInfo[];
  selected: Set<string>;
  onToggle: (tab: TabInfo, checked: boolean) => void;
  onDelete: (tab: TabInfo) => void;
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-10" />
          <TableHead>Tab</TableHead>
          <TableHead>URL</TableHead>
          <TableHead>Viewport</TableHead>
          <TableHead>Viewers</TableHead>
          <TableHead className="text-right">Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {!tabs.length && <EmptyRow colSpan={6}>No idle tabs available</EmptyRow>}
        {tabs.map((tab) => {
          const key = tabKey(tab.browser_id, tab.tab_id);
          const viewport = tab.viewport || { width: 1280, height: 720 };
          return (
            <TableRow key={key}>
              <TableCell>
                <input
                  type="checkbox"
                  className="h-4 w-4 rounded border-input"
                  checked={selected.has(key)}
                  onChange={(event) => onToggle(tab, event.target.checked)}
                />
              </TableCell>
              <TableCell className="font-medium">
                {tab.browser_id}/{tab.tab_id}
              </TableCell>
              <TableCell>
                <div className="max-w-[520px] truncate" title={tab.url || ""}>
                  {truncate(tab.url || "about:blank", 92)}
                </div>
              </TableCell>
              <TableCell>
                {viewport.width}x{viewport.height}
              </TableCell>
              <TableCell>{tab.viewer_count || 0}</TableCell>
              <TableCell>
                <div className="flex justify-end gap-2">
                  <Button size="sm" variant="outline" asChild>
                    <a href={`/viewer/${tab.browser_id}/${tab.tab_id}`} target="_blank" rel="noreferrer">
                      <ExternalLink className="h-4 w-4" />
                      View
                    </a>
                  </Button>
                  <Button size="sm" variant="destructive" onClick={() => onDelete(tab)}>
                    <Trash2 className="h-4 w-4" />
                    Close
                  </Button>
                </div>
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

function WorkbenchTable({
  workbenches,
  selectedCount,
  onCreateWorkbench,
  onAddTabs,
  onRemoveTabs,
  onRemoveTab,
  onToggleRecording,
  recordingBusyIds,
  onDelete,
}: {
  workbenches: Workbench[];
  selectedCount: number;
  onCreateWorkbench?: () => void;
  onAddTabs: (workbench: Workbench) => void;
  onRemoveTabs: (workbench: Workbench) => void;
  onRemoveTab: (workbench: Workbench, tab: TabInfo) => void;
  onToggleRecording: (workbench: Workbench) => void;
  recordingBusyIds: Set<number>;
  onDelete: (workbench: Workbench) => void;
}) {
  const [expandedWorkbenchIds, setExpandedWorkbenchIds] = useState<Set<number>>(new Set());

  function toggleExpanded(workbenchId: number) {
    setExpandedWorkbenchIds((current) => {
      const next = new Set(current);
      if (next.has(workbenchId)) next.delete(workbenchId);
      else next.add(workbenchId);
      return next;
    });
  }

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-3">
        <div>
          <CardTitle>Workbenches</CardTitle>
          <CardDescription>
            {workbenches.length} persisted groups / {selectedCount} selected
          </CardDescription>
        </div>
        {onCreateWorkbench && (
          <Button size="sm" disabled={!selectedCount} onClick={onCreateWorkbench}>
            <CirclePlus className="h-4 w-4" />
            Workbench
          </Button>
        )}
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Details</TableHead>
              <TableHead>ID</TableHead>
              <TableHead>Name</TableHead>
              <TableHead>Tabs</TableHead>
              <TableHead>Current</TableHead>
              <TableHead>Recording</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {!workbenches.length && <EmptyRow colSpan={7}>No workbenches created</EmptyRow>}
            {workbenches.map((workbench) => {
              const expanded = expandedWorkbenchIds.has(workbench.id);
              const currentTab =
                workbench.active_browser_id != null && workbench.active_tab_id != null
                  ? `${workbench.active_browser_id}/${workbench.active_tab_id}`
                  : "-";
              const recordingBusy = recordingBusyIds.has(workbench.id);
              return (
                <Fragment key={workbench.id}>
                  <TableRow>
                    <TableCell>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => toggleExpanded(workbench.id)}
                        aria-label={expanded ? "Collapse workbench tabs" : "Expand workbench tabs"}
                      >
                        {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                        Tabs
                      </Button>
                    </TableCell>
                    <TableCell className="font-medium">{workbench.id}</TableCell>
                    <TableCell>{workbench.name || "Workbench"}</TableCell>
                    <TableCell>{workbench.tab_count}</TableCell>
                    <TableCell>
                      <div className="font-medium">{currentTab}</div>
                      <div className="text-xs text-muted-foreground">{workbench.active_tab_count} live</div>
                    </TableCell>
                    <TableCell>
                      <div className="grid gap-1">
                        <Badge variant={workbench.recording_enabled ? "destructive" : "muted"}>
                          {workbench.recording_enabled ? "recording" : "idle"}
                        </Badge>
                        {workbench.recording_room && (
                          <div className="max-w-[180px] truncate text-xs text-muted-foreground" title={workbench.recording_file || ""}>
                            {workbench.recording_room}
                          </div>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex justify-end gap-2">
                        <Button
                          size="sm"
                          variant={workbench.recording_enabled ? "warning" : "outline"}
                          disabled={recordingBusy || !workbench.active_room}
                          onClick={() => onToggleRecording(workbench)}
                        >
                          {workbench.recording_enabled ? <Square className="h-4 w-4" /> : <CirclePlus className="h-4 w-4" />}
                          {workbench.recording_enabled ? "Stop" : "Record"}
                        </Button>
                        <Button size="sm" variant="outline" asChild>
                          <a href={workbench.url} target="_blank" rel="noreferrer">
                            <ExternalLink className="h-4 w-4" />
                            Open
                          </a>
                        </Button>
                        <Button size="sm" disabled={!selectedCount} onClick={() => onAddTabs(workbench)}>
                          <CirclePlus className="h-4 w-4" />
                          Add
                        </Button>
                        <Button
                          size="sm"
                          variant="warning"
                          disabled={workbench.tab_count <= 1}
                          onClick={() => onRemoveTabs(workbench)}
                        >
                          <X className="h-4 w-4" />
                          Remove
                        </Button>
                        <Button size="sm" variant="destructive" onClick={() => onDelete(workbench)}>
                          <Trash2 className="h-4 w-4" />
                          Delete
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                  {expanded && (
                    <TableRow>
                      <TableCell colSpan={7} className="bg-muted/30 p-0">
                        <WorkbenchTabsPanel workbench={workbench} onRemoveTab={onRemoveTab} />
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              );
            })}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function WorkbenchTabsPanel({
  workbench,
  onRemoveTab,
}: {
  workbench: Workbench;
  onRemoveTab: (workbench: Workbench, tab: TabInfo) => void;
}) {
  const canRelease = workbench.tab_count > 1;

  return (
    <div className="px-4 py-3">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Tab</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>URL</TableHead>
            <TableHead>Viewport</TableHead>
            <TableHead>Viewers</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {!workbench.tabs.length && <EmptyRow colSpan={6}>No tabs in this workbench</EmptyRow>}
          {workbench.tabs.map((tab) => {
            const viewport = tab.viewport || { width: 0, height: 0 };
            const isCurrent = tab.browser_id === workbench.active_browser_id && tab.tab_id === workbench.active_tab_id;
            return (
              <TableRow key={tabKey(tab.browser_id, tab.tab_id)}>
                <TableCell className="font-medium">
                  <div className="flex items-center gap-2">
                    {tab.browser_id}/{tab.tab_id}
                    {isCurrent && <Badge variant="default">current</Badge>}
                  </div>
                </TableCell>
                <TableCell>
                  <Badge variant={tab.available === false ? "destructive" : "success"}>
                    {tab.available === false ? "missing" : "active"}
                  </Badge>
                </TableCell>
                <TableCell>
                  <div className="max-w-[520px] truncate" title={tab.url || tab.allowed_hostname || ""}>
                    {truncate(tab.url || tab.allowed_hostname || "missing", 92)}
                  </div>
                </TableCell>
                <TableCell>{viewport.width && viewport.height ? `${viewport.width}x${viewport.height}` : "-"}</TableCell>
                <TableCell>{tab.viewer_count || 0}</TableCell>
                <TableCell>
                  <div className="flex justify-end gap-2">
                    {tab.available === false ? (
                      <Button size="sm" variant="outline" disabled>
                        <ExternalLink className="h-4 w-4" />
                        View
                      </Button>
                    ) : (
                      <Button size="sm" variant="outline" asChild>
                        <a href={`/viewer/${tab.browser_id}/${tab.tab_id}`} target="_blank" rel="noreferrer">
                          <ExternalLink className="h-4 w-4" />
                          View
                        </a>
                      </Button>
                    )}
                    <Button
                      size="sm"
                      variant="warning"
                      disabled={!canRelease}
                      onClick={() => onRemoveTab(workbench, tab)}
                      title={canRelease ? undefined : "Delete the workbench to release its last tab"}
                    >
                      <X className="h-4 w-4" />
                      Release
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

function ViewersTable({
  viewers,
  onKick,
}: {
  viewers: ViewerInfo[];
  onKick: (viewer: ViewerInfo, viewerIndex: number) => void;
}) {
  const countsByTab = new Map<string, number>();

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Tab</TableHead>
          <TableHead>Client</TableHead>
          <TableHead>URL</TableHead>
          <TableHead className="text-right">Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {!viewers.length && <EmptyRow colSpan={4}>No active viewers</EmptyRow>}
        {viewers.map((viewer) => {
          const groupKey = tabKey(viewer.browser_id, viewer.tab_id);
          const viewerIndex = countsByTab.get(groupKey) || 0;
          countsByTab.set(groupKey, viewerIndex + 1);
          return (
            <TableRow key={viewer.id}>
              <TableCell className="font-medium">
                {viewer.browser_id}/{viewer.tab_id}
              </TableCell>
              <TableCell>
                {viewer.client_host}:{viewer.client_port}
              </TableCell>
              <TableCell>{truncate(viewer.current_url || "", 96)}</TableCell>
              <TableCell className="text-right">
                <Button size="sm" variant="destructive" onClick={() => onKick(viewer, viewerIndex)}>
                  Kick
                </Button>
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

function CreateBrowserDialog({
  open,
  onOpenChange,
  form,
  setForm,
  chromeVersions,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  form: {
    name: string;
    proxy: string;
    user_data_dir: string;
    chrome_version: string;
    auto_start: boolean;
  };
  setForm: React.Dispatch<React.SetStateAction<typeof form>>;
  chromeVersions: string[];
  onSubmit: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create Browser</DialogTitle>
        </DialogHeader>
        <div className="grid gap-4">
          <Field label="Name">
            <Input value={form.name} onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))} />
          </Field>
          <Field label="Proxy">
            <Input value={form.proxy} onChange={(event) => setForm((prev) => ({ ...prev, proxy: event.target.value }))} />
          </Field>
          <Field label="Data directory">
            <Input value={form.user_data_dir} onChange={(event) => setForm((prev) => ({ ...prev, user_data_dir: event.target.value }))} />
          </Field>
          <Field label="Chrome version">
            <select
              className="h-9 rounded-md border bg-background px-3 text-sm"
              value={form.chrome_version}
              onChange={(event) => setForm((prev) => ({ ...prev, chrome_version: event.target.value }))}
            >
              <option value="">Auto</option>
              {chromeVersions.map((version) => (
                <option key={version} value={version}>
                  {version}
                </option>
              ))}
            </select>
          </Field>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.auto_start}
              onChange={(event) => setForm((prev) => ({ ...prev, auto_start: event.target.checked }))}
            />
            Auto start
          </label>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={onSubmit}>Create</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function EditBrowserDialog(props: Omit<Parameters<typeof CreateBrowserDialog>[0], "open"> & { browser: BrowserInfo | null }) {
  const { browser, onOpenChange, form, setForm, chromeVersions, onSubmit } = props;
  return (
    <Dialog open={Boolean(browser)} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit Browser</DialogTitle>
        </DialogHeader>
        <div className="grid gap-4">
          <Field label="Name">
            <Input value={form.name} onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))} />
          </Field>
          <Field label="Proxy">
            <Input value={form.proxy} onChange={(event) => setForm((prev) => ({ ...prev, proxy: event.target.value }))} />
          </Field>
          <Field label="Chrome version">
            <select
              className="h-9 rounded-md border bg-background px-3 text-sm"
              value={form.chrome_version}
              onChange={(event) => setForm((prev) => ({ ...prev, chrome_version: event.target.value }))}
            >
              <option value="">Auto</option>
              {chromeVersions.map((version) => (
                <option key={version} value={version}>
                  {version}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={onSubmit}>Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CreateTabDialog({
  browser,
  onOpenChange,
  form,
  setForm,
  onSubmit,
}: {
  browser: BrowserInfo | null;
  onOpenChange: (open: boolean) => void;
  form: { url: string; viewport_width: number; viewport_height: number };
  setForm: React.Dispatch<React.SetStateAction<{ url: string; viewport_width: number; viewport_height: number }>>;
  onSubmit: () => void;
}) {
  return (
    <Dialog open={Boolean(browser)} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create Tab</DialogTitle>
          <DialogDescription>{browser ? `Browser ${browser.browser_id}` : ""}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <Field label="URL">
            <Input value={form.url} onChange={(event) => setForm((prev) => ({ ...prev, url: event.target.value }))} />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Width">
              <Input
                type="number"
                value={form.viewport_width}
                onChange={(event) => setForm((prev) => ({ ...prev, viewport_width: Number(event.target.value) }))}
              />
            </Field>
            <Field label="Height">
              <Input
                type="number"
                value={form.viewport_height}
                onChange={(event) => setForm((prev) => ({ ...prev, viewport_height: Number(event.target.value) }))}
              />
            </Field>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={onSubmit}>Create</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CreateWorkbenchDialog({
  open,
  onOpenChange,
  selectedCount,
  name,
  setName,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  selectedCount: number;
  name: string;
  setName: (name: string) => void;
  onSubmit: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create Workbench</DialogTitle>
          <DialogDescription>{selectedCount} selected</DialogDescription>
        </DialogHeader>
        <Field label="Name">
          <Input value={name} onChange={(event) => setName(event.target.value)} />
        </Field>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={onSubmit} disabled={!selectedCount}>
            Create
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function AddTabsDialog({
  workbench,
  selectedTabs,
  onOpenChange,
  onSubmit,
}: {
  workbench: Workbench | null;
  selectedTabs: { browser_id: number; tab_id: number }[];
  onOpenChange: (open: boolean) => void;
  onSubmit: () => void;
}) {
  return (
    <Dialog open={Boolean(workbench)} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add Tabs</DialogTitle>
          <DialogDescription>{workbench ? `${workbench.name || "Workbench"} (#${workbench.id})` : ""}</DialogDescription>
        </DialogHeader>
        <div className="rounded-md border bg-muted/40 p-3 text-sm">
          {selectedTabs.map((tab) => `${tab.browser_id}/${tab.tab_id}`).join(", ") || "No tabs selected"}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={onSubmit} disabled={!selectedTabs.length}>
            Add
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function RemoveTabsDialog({
  workbench,
  selected,
  setSelected,
  removesAll,
  onOpenChange,
  onSubmit,
}: {
  workbench: Workbench | null;
  selected: Set<string>;
  setSelected: React.Dispatch<React.SetStateAction<Set<string>>>;
  removesAll: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: () => void;
}) {
  const tabs = workbench?.tabs || [];
  return (
    <Dialog open={Boolean(workbench)} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Remove Tabs</DialogTitle>
          <DialogDescription>{workbench ? `${workbench.name || "Workbench"} (#${workbench.id})` : ""}</DialogDescription>
        </DialogHeader>
        <div className="grid max-h-[360px] gap-2 overflow-auto">
          {tabs.map((tab) => {
            const key = tabKey(tab.browser_id, tab.tab_id);
            return (
              <label key={key} className="flex items-center gap-3 rounded-md border p-3 text-sm">
                <input
                  type="checkbox"
                  className="h-4 w-4"
                  checked={selected.has(key)}
                  onChange={(event) => {
                    setSelected((current) => {
                      const next = new Set(current);
                      if (event.target.checked) next.add(key);
                      else next.delete(key);
                      return next;
                    });
                  }}
                />
                <span className="font-medium">
                  {tab.browser_id}/{tab.tab_id}
                </span>
                <span className="truncate text-muted-foreground">{tab.url || tab.allowed_hostname || "missing"}</span>
              </label>
            );
          })}
        </div>
        {removesAll && <div className="text-sm text-red-600">Cannot remove all tabs. Delete the workbench instead.</div>}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={onSubmit} disabled={!selected.size || removesAll}>
            Remove
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DeleteWorkbenchDialog({
  workbench,
  onOpenChange,
  onSubmit,
}: {
  workbench: Workbench | null;
  onOpenChange: (open: boolean) => void;
  onSubmit: () => void;
}) {
  return (
    <Dialog open={Boolean(workbench)} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete Workbench</DialogTitle>
          <DialogDescription>{workbench ? `${workbench.name || "Workbench"} (#${workbench.id})` : ""}</DialogDescription>
        </DialogHeader>
        <div className="rounded-md border bg-muted/40 p-3 text-sm">
          {(workbench?.tabs || []).map((tab) => `${tab.browser_id}/${tab.tab_id}`).join(", ") || "No tabs"}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={onSubmit}>
            Delete
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
