// Main page

"use client";

import { useState, useCallback } from "react";
import { useDropzone } from "react-dropzone";
import toast from "react-hot-toast";
import { apiClient, downloadBlob, type Customer, type PricingPlan, type FieldMapping, type IngestResponse, type PreviewResponse } from "../lib/api";
import {
  Upload, Cpu, FileText, CheckCircle2, AlertTriangle,
  Download, ChevronRight, Zap, Database, RefreshCw,
  TrendingUp, DollarSign, Activity, Shield, Sparkles, Eye,
  ArrowRight, Info, AlertCircle
} from "lucide-react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from "recharts";

//Types

type Step = "upload" | "mapping" | "preview" | "export";
type Scenario = "normal" | "spike" | "messy" | "enterprise";

const STEPS: { id: Step; label: string; icon: any }[] = [
  { id: "upload", label: "Upload Data", icon: Upload },
  { id: "mapping", label: "Field Mapping", icon: Cpu },
  { id: "preview", label: "Billing Preview", icon: Eye },
  { id: "export", label: "Export", icon: Download },
];

const CANONICAL_FIELDS = [
  "api_calls", "compute_hours", "storage_gb", "active_seats",
  "data_transfer_gb", "timestamp", "customer_id", "quantity", "unknown"
];

const METRIC_COLORS: Record<string, string> = {
  api_calls: "#6172f3",
  compute_hours: "#f59e0b",
  storage_gb: "#10b981",
  active_seats: "#8b5cf6",
  data_transfer_gb: "#06b6d4",
};

// Confidence Badge

function ConfidenceBadge({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const cls = value >= 0.85 ? "confidence-high" : value >= 0.6 ? "confidence-medium" : "confidence-low";
  return (
    <span className={`font-mono text-xs font-semibold ${cls}`}>
      {pct}%
    </span>
  );
}

//Severity Icon

function SeverityBadge({ severity }: { severity: string }) {
  if (severity === "critical") return <span className="badge-critical"><AlertCircle size={10} /> Critical</span>;
  if (severity === "warning") return <span className="badge-warning"><AlertTriangle size={10} /> Warning</span>;
  return <span className="badge-info"><Info size={10} /> Info</span>;
}

// Step Indicator

function StepBar({ current }: { current: Step }) {
  const idx = STEPS.findIndex(s => s.id === current);
  return (
    <div className="flex items-center gap-2">
      {STEPS.map((step, i) => {
        const Icon = step.icon;
        const done = i < idx;
        const active = i === idx;
        return (
          <div key={step.id} className="flex items-center gap-2">
            <div className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-semibold transition-all ${
              done ? "step-complete" : active ? "step-active" : "step-inactive"
            }`}>
              <Icon size={12} />
              {step.label}
            </div>
            {i < STEPS.length - 1 && (
              <ChevronRight size={12} className={done ? "text-brand-400" : "text-slate-700"} />
            )}
          </div>
        );
      })}
    </div>
  );
}

// Main App

export default function Home() {
  const [step, setStep] = useState<Step>("upload");
  const [loading, setLoading] = useState(false);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [plans, setPlans] = useState<PricingPlan[]>([]);
  const [selectedCustomer, setSelectedCustomer] = useState<string>("");
  const [selectedPlan, setSelectedPlan] = useState<string>("");
  const [jobId, setJobId] = useState<string>("");
  const [ingestResult, setIngestResult] = useState<IngestResponse | null>(null);
  const [mappings, setMappings] = useState<FieldMapping[]>([]);
  const [previewResult, setPreviewResult] = useState<PreviewResponse | null>(null);
  const [mockScenario, setMockScenario] = useState<Scenario>("messy");
  const [uploadedFile, setUploadedFile] = useState<File | null>(null);

  // Load customers and plans on first render
  useState(() => {
    const load = async () => {
      try {
        const [cRes, pRes] = await Promise.all([
          apiClient.getCustomers(),
          apiClient.getPricingPlans(),
        ]);
        setCustomers(cRes.data);
        setPlans(pRes.data);
        if (cRes.data[0]) setSelectedCustomer(cRes.data[0].id);
        if (pRes.data[0]) setSelectedPlan(pRes.data[0].id);
      } catch { /* API might not be running yet */ }
    };
    load();
  });

  // Step 1: Upload

  const onDrop = useCallback(async (files: File[]) => {
    const file = files[0];
    if (!file) return;
    setUploadedFile(file);
    await handleIngest(file);
  }, [selectedCustomer]);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { "text/csv": [".csv"], "application/json": [".json"] },
    multiple: false,
  });

  const handleIngest = async (file: File) => {
    setLoading(true);
    try {
      const isCSV = file.name.endsWith(".csv");
      const res = isCSV
        ? await apiClient.ingestCSV(file, selectedCustomer)
        : await apiClient.ingestJSON(file, selectedCustomer);

      setIngestResult(res.data);
      setJobId(res.data.job_id);
      setMappings(res.data.suggested_mappings);
      setStep("mapping");
      toast.success(`Parsed ${res.data.row_count.toLocaleString()} rows — review field mappings`);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || "Failed to parse file");
    } finally {
      setLoading(false);
    }
  };

  const handleMockDownloadAndUpload = async () => {
    setLoading(true);
    try {
      const res = await apiClient.generateMockData(selectedCustomer || "b2c3d4e5-0000-0000-0000-000000000001", mockScenario, 30);
      const blob = new Blob([res.data], { type: "text/csv" });
      const filename = `mock_${mockScenario}.csv`;
      const file = new File([blob], filename, { type: "text/csv" });
      setUploadedFile(file);
      await handleIngest(file);
    } catch (e: any) {
      toast.error("Failed to generate mock data");
    } finally {
      setLoading(false);
    }
  };

  // Step 2: Mappings 

  const updateMapping = (sourceField: string, targetField: string) => {
    setMappings(prev => prev.map(m =>
      m.source_field === sourceField
        ? { ...m, target_field: targetField, is_confirmed: true }
        : m
    ));
  };

  const confirmAllMappings = async () => {
    setLoading(true);
    try {
      await apiClient.updateMappings(jobId, mappings.map(m => ({
        source_field: m.source_field,
        target_field: m.target_field,
        is_confirmed: true,
      })));
      toast.success("Mappings confirmed — normalizing data...");
      await apiClient.normalizeJob(jobId);
      await handleGeneratePreview();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || "Failed to confirm mappings");
      setLoading(false);
    }
  };

  // Step 3: Preview 

  const handleGeneratePreview = async () => {
    setLoading(true);
    try {
      const res = await apiClient.generatePreview(jobId, selectedPlan || undefined);
      setPreviewResult(res.data);
      setStep("preview");
      const warningCount = res.data.warnings.filter(w => w.severity !== "info").length;
      if (warningCount > 0) {
        toast(`${warningCount} billing warning${warningCount > 1 ? "s" : ""} detected`, { icon: "⚠️" });
      } else {
        toast.success("Billing preview generated successfully");
      }
    } catch (e: any) {
      toast.error(e.response?.data?.detail || "Failed to generate preview");
    } finally {
      setLoading(false);
    }
  };

  // Step 4: Export

  const handleExport = async (format: string) => {
    setLoading(true);
    try {
      const res = await apiClient.exportJob(jobId, format);
      const ext = format === "csv" ? "csv" : "json";
      downloadBlob(res.data, `export_${jobId.slice(0, 8)}.${ext}`);
      toast.success(`Exported as ${format.toUpperCase()}`);
    } catch (e: any) {
      toast.error("Export failed");
    } finally {
      setLoading(false);
    }
  };

  const reset = () => {
    setStep("upload");
    setJobId("");
    setIngestResult(null);
    setMappings([]);
    setPreviewResult(null);
    setUploadedFile(null);
  };



  return (
    <div className="min-h-screen bg-slate-950 bg-grid-pattern bg-grid">
      {/* Background glow */}
      <div className="fixed inset-0 bg-glow-brand pointer-events-none" />

      {/* Header */}
      <header className="sticky top-0 z-50 glass border-b border-subtle">
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-brand-600 flex items-center justify-center glow-xs">
              <Zap size={16} className="text-white" />
            </div>
            <div>
              <span className="font-display font-bold text-lg text-white tracking-tight">BillingLens</span>
              <span className="ml-2 text-xs text-slate-500 hidden sm:inline">Usage Ingestion & Preview</span>
            </div>
          </div>

          <div className="flex items-center gap-4">
            <StepBar current={step} />
            {step !== "upload" && (
              <button onClick={reset} className="btn-secondary text-xs">
                <RefreshCw size={12} /> New Upload
              </button>
            )}
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-8 space-y-6 animate-in">

        {/* ── STEP 1: UPLOAD ── */}
        {step === "upload" && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Drop zone */}
            <div className="lg:col-span-2 space-y-4">
              <div>
                <h1 className="font-display text-3xl font-bold text-white">
                  Ingest Usage Data
                </h1>
                <p className="text-slate-400 mt-1">
                  Drop a CSV or JSON file. AI maps messy column names to billing metrics automatically.
                </p>
              </div>

              {/* Customer selector */}
              <div className="card">
                <label className="text-xs font-semibold uppercase tracking-widest text-slate-500 block mb-2">
                  Customer
                </label>
                <select
                  value={selectedCustomer}
                  onChange={e => setSelectedCustomer(e.target.value)}
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-brand-500"
                >
                  <option value="">— No customer (demo) —</option>
                  {customers.map(c => (
                    <option key={c.id} value={c.id}>{c.name} ({c.email})</option>
                  ))}
                </select>
              </div>

              {/* Dropzone */}
              <div
                {...getRootProps()}
                className={`relative border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition-all duration-200 ${
                  isDragActive
                    ? "border-brand-400 bg-brand-500/10"
                    : "border-slate-700 hover:border-brand-600 hover:bg-brand-500/5"
                }`}
              >
                <input {...getInputProps()} />
                <div className="flex flex-col items-center gap-3">
                  <div className={`w-14 h-14 rounded-2xl flex items-center justify-center transition-all ${
                    isDragActive ? "bg-brand-500/30" : "bg-slate-800"
                  }`}>
                    <Upload size={24} className={isDragActive ? "text-brand-400" : "text-slate-500"} />
                  </div>
                  <div>
                    <p className="font-semibold text-white">
                      {isDragActive ? "Drop it!" : "Drop CSV or JSON file"}
                    </p>
                    <p className="text-sm text-slate-500 mt-1">
                      or click to browse · up to 50MB
                    </p>
                  </div>
                  <div className="flex gap-2 mt-1">
                    {["CSV", "JSON", "Webhook"].map(f => (
                      <span key={f} className="px-2 py-0.5 rounded text-xs bg-slate-800 text-slate-400 font-mono">{f}</span>
                    ))}
                  </div>
                </div>
                {loading && (
                  <div className="absolute inset-0 rounded-xl bg-slate-950/80 flex items-center justify-center">
                    <div className="flex items-center gap-3 text-brand-400">
                      <Cpu size={20} className="animate-spin" />
                      <span className="text-sm font-medium">Parsing & analyzing...</span>
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Mock data panel */}
            <div className="space-y-4">
              <div className="card">
                <h3 className="font-display font-semibold text-white mb-1 flex items-center gap-2">
                  <Sparkles size={14} className="text-brand-400" />
                  Try with Mock Data
                </h3>
                <p className="text-xs text-slate-500 mb-4">
                  Generate realistic test data to demo the full pipeline
                </p>

                <div className="space-y-2 mb-4">
                  {(["normal", "spike", "messy", "enterprise"] as Scenario[]).map(s => (
                    <button
                      key={s}
                      onClick={() => setMockScenario(s)}
                      className={`w-full text-left px-3 py-2.5 rounded-lg text-sm transition-all ${
                        mockScenario === s
                          ? "bg-brand-600/20 border border-brand-500/40 text-brand-300"
                          : "bg-slate-900 border border-slate-800 text-slate-400 hover:text-slate-300"
                      }`}
                    >
                      <div className="font-medium capitalize">{s === "messy" ? "🗂️ Messy Data (recommended)" : s === "spike" ? "📈 Usage Spike" : s === "normal" ? "✅ Normal Usage" : "🏢 Enterprise Scale"}</div>
                      <div className="text-xs mt-0.5 opacity-70">
                        {s === "messy" ? "Real-world messy CSV from homegrown system" :
                         s === "spike" ? "Normal data with a 15x outlier day" :
                         s === "normal" ? "Clean, predictable daily usage" :
                         "High-volume enterprise data"}
                      </div>
                    </button>
                  ))}
                </div>

                <button
                  onClick={handleMockDownloadAndUpload}
                  disabled={loading}
                  className="btn-primary w-full justify-center"
                >
                  {loading ? <Cpu size={14} className="animate-spin" /> : <Zap size={14} />}
                  {loading ? "Generating..." : "Generate & Upload"}
                </button>
              </div>

              {/* Feature callouts */}
              <div className="card space-y-3">
                <h3 className="text-xs font-semibold uppercase tracking-widest text-slate-500">Features</h3>
                {[
                  { icon: Cpu, label: "AI Field Mapping", desc: "Confidence scores + manual override" },
                  { icon: Shield, label: "Anomaly Detection", desc: "Spike, zero, negative value checks" },
                  { icon: DollarSign, label: "Billing Preview", desc: "Tiered, flat-rate, volume pricing" },
                  { icon: Download, label: "Standard Export", desc: "Clean normalized output format" },
                ].map(f => (
                  <div key={f.label} className="flex items-start gap-3">
                    <div className="w-7 h-7 rounded-lg bg-brand-600/15 flex items-center justify-center flex-shrink-0 mt-0.5">
                      <f.icon size={12} className="text-brand-400" />
                    </div>
                    <div>
                      <div className="text-sm font-medium text-slate-300">{f.label}</div>
                      <div className="text-xs text-slate-600">{f.desc}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* ── STEP 2: FIELD MAPPING ── */}
        {step === "mapping" && ingestResult && (
          <div className="space-y-5 animate-in">
            <div className="flex items-start justify-between">
              <div>
                <h2 className="font-display text-2xl font-bold text-white">Field Mapping</h2>
                <p className="text-slate-400 mt-1">
                  AI detected <strong className="text-white">{ingestResult.columns_detected.length} columns</strong> in{" "}
                  <strong className="text-white">{ingestResult.row_count.toLocaleString()} rows</strong>.
                  Review and confirm the mappings below.
                </p>
              </div>
              <button
                onClick={confirmAllMappings}
                disabled={loading}
                className="btn-primary"
              >
                {loading ? <Cpu size={14} className="animate-spin" /> : <CheckCircle2 size={14} />}
                {loading ? "Normalizing..." : "Confirm & Preview"}
                <ArrowRight size={14} />
              </button>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
              {/* Mapping table */}
              <div className="lg:col-span-2 card">
                <h3 className="text-sm font-semibold text-slate-300 mb-4 flex items-center gap-2">
                  <Cpu size={14} className="text-brand-400" />
                  AI-Suggested Mappings
                  <span className="ml-auto text-xs text-slate-600">
                    {mappings.filter(m => m.confidence >= 0.85).length}/{mappings.length} auto-confirmed
                  </span>
                </h3>

                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Source Column</th>
                      <th>Maps To</th>
                      <th>Confidence</th>
                      <th>Method</th>
                    </tr>
                  </thead>
                  <tbody>
                    {mappings.map(m => (
                      <tr key={m.source_field}>
                        <td>
                          <code className="text-brand-300 font-mono text-xs bg-brand-500/10 px-2 py-0.5 rounded">
                            {m.source_field}
                          </code>
                        </td>
                        <td>
                          <select
                            value={m.target_field}
                            onChange={e => updateMapping(m.source_field, e.target.value)}
                            className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-xs text-white focus:outline-none focus:border-brand-500 font-mono"
                          >
                            {CANONICAL_FIELDS.map(f => (
                              <option key={f} value={f}>{f}</option>
                            ))}
                          </select>
                        </td>
                        <td><ConfidenceBadge value={m.confidence} /></td>
                        <td>
                          <span className={`text-xs px-1.5 py-0.5 rounded font-mono ${
                            m.mapping_method === "ai" ? "bg-purple-500/15 text-purple-400" :
                            m.mapping_method === "rule" ? "bg-blue-500/15 text-blue-400" :
                            "bg-slate-700 text-slate-400"
                          }`}>
                            {m.mapping_method}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Sample data preview */}
              <div className="card">
                <h3 className="text-sm font-semibold text-slate-300 mb-3 flex items-center gap-2">
                  <FileText size={14} className="text-slate-500" />
                  Sample Rows
                </h3>
                <div className="overflow-x-auto">
                  <div className="space-y-2">
                    {ingestResult.sample_rows.slice(0, 4).map((row, i) => (
                      <div key={i} className="bg-slate-900 rounded-lg p-3 font-mono text-xs">
                        {Object.entries(row).slice(0, 4).map(([k, v]) => (
                          <div key={k} className="flex gap-2">
                            <span className="text-slate-600 min-w-0 truncate">{k}:</span>
                            <span className="text-emerald-400 truncate">{String(v)}</span>
                          </div>
                        ))}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ── STEP 3: BILLING PREVIEW ── */}
        {step === "preview" && previewResult && (
          <div className="space-y-5 animate-in">
            <div className="flex items-start justify-between">
              <div>
                <h2 className="font-display text-2xl font-bold text-white">Billing Preview</h2>
                <p className="text-slate-400 mt-1">
                  {previewResult.preview.period_start} → {previewResult.preview.period_end}
                  {" · "}
                  <span className="text-brand-400 font-medium">
                    {previewResult.warnings.filter(w => w.severity !== "info").length} warnings
                  </span>
                </p>
              </div>
              <button onClick={() => setStep("export")} className="btn-primary">
                Export Data <ArrowRight size={14} />
              </button>
            </div>

            {/* Summary cards */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <div className="card text-center">
                <div className="text-3xl font-display font-bold text-white">
                  ${previewResult.preview.total.toLocaleString("en-US", { minimumFractionDigits: 2 })}
                </div>
                <div className="text-xs text-slate-500 mt-1 uppercase tracking-widest">Total Due</div>
              </div>
              <div className="card text-center">
                <div className="text-3xl font-display font-bold text-brand-400">
                  {previewResult.preview.line_items.length}
                </div>
                <div className="text-xs text-slate-500 mt-1 uppercase tracking-widest">Line Items</div>
              </div>
              <div className="card text-center">
                <div className="text-3xl font-display font-bold text-amber-400">
                  {previewResult.warnings.filter(w => w.severity === "warning" || w.severity === "critical").length}
                </div>
                <div className="text-xs text-slate-500 mt-1 uppercase tracking-widest">Warnings</div>
              </div>
              <div className="card text-center">
                <div className="text-3xl font-display font-bold text-emerald-400">
                  {previewResult.usage_summary.length}
                </div>
                <div className="text-xs text-slate-500 mt-1 uppercase tracking-widest">Metrics Found</div>
              </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
              {/* Line items */}
              <div className="lg:col-span-2 card">
                <h3 className="text-sm font-semibold text-slate-300 mb-4 flex items-center gap-2">
                  <DollarSign size={14} className="text-brand-400" />
                  Invoice Line Items
                </h3>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Metric</th>
                      <th>Usage</th>
                      <th>Billable</th>
                      <th>Model</th>
                      <th className="text-right">Amount</th>
                    </tr>
                  </thead>
                  <tbody>
                    {previewResult.preview.line_items.map((item: any) => (
                      <tr key={item.metric_name}>
                        <td>
                          <div className="flex items-center gap-2">
                            <div
                              className="w-2 h-2 rounded-full"
                              style={{ background: METRIC_COLORS[item.metric_name] || "#6b7280" }}
                            />
                            <span className="font-medium text-white">{item.display_name}</span>
                          </div>
                        </td>
                        <td className="font-mono text-xs">
                          {Number(item.total_quantity).toLocaleString()} {item.unit_label}
                        </td>
                        <td className="font-mono text-xs">
                          {Number(item.billable_quantity).toLocaleString()}
                          {item.free_tier_used > 0 && (
                            <span className="ml-1 text-emerald-500 text-xs">
                              ({Number(item.free_tier_used).toLocaleString()} free)
                            </span>
                          )}
                        </td>
                        <td>
                          <span className="text-xs font-mono bg-slate-800 px-1.5 py-0.5 rounded text-slate-400">
                            {item.pricing_model}
                          </span>
                        </td>
                        <td className="text-right font-mono font-semibold text-white">
                          ${Number(item.amount).toFixed(2)}
                        </td>
                      </tr>
                    ))}
                    <tr>
                      <td colSpan={4} className="text-right font-semibold text-slate-400 !border-t-2 !border-slate-700">
                        Total
                      </td>
                      <td className="text-right font-display font-bold text-xl text-white !border-t-2 !border-slate-700">
                        ${previewResult.preview.total.toFixed(2)}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>

              {/* Warnings + chart */}
              <div className="space-y-4">
                {/* Usage chart */}
                <div className="card">
                  <h3 className="text-sm font-semibold text-slate-300 mb-3 flex items-center gap-2">
                    <Activity size={14} className="text-brand-400" />
                    Metric Breakdown
                  </h3>
                  <ResponsiveContainer width="100%" height={160}>
                    <BarChart data={previewResult.preview.line_items.filter((i: any) => i.amount > 0)} margin={{ top: 0, right: 0, bottom: 0, left: 0 }}>
                      <XAxis dataKey="display_name" tick={{ fontSize: 9, fill: "#64748b" }} />
                      <YAxis hide />
                      <Tooltip
                        formatter={(val: any) => [`$${Number(val).toFixed(2)}`, "Charge"]}
                        contentStyle={{ background: "#0f1523", border: "1px solid #3d4f74", borderRadius: 8, fontSize: 12 }}
                      />
                      <Bar dataKey="amount" radius={[4, 4, 0, 0]}>
                        {previewResult.preview.line_items
                          .filter((i: any) => i.amount > 0)
                          .map((item: any, idx: number) => (
                          <Cell key={idx} fill={METRIC_COLORS[item.metric_name] || "#6172f3"} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>

                {/* Warnings */}
                {previewResult.warnings.length > 0 && (
                  <div className="card">
                    <h3 className="text-sm font-semibold text-slate-300 mb-3 flex items-center gap-2">
                      <AlertTriangle size={14} className="text-amber-400" />
                      Validation Warnings
                    </h3>
                    <div className="space-y-2">
                      {previewResult.warnings.map((w, i) => (
                        <div key={i} className="p-2.5 rounded-lg bg-slate-900 border border-slate-800">
                          <div className="flex items-start justify-between gap-2 mb-1">
                            <SeverityBadge severity={w.severity} />
                          </div>
                          <p className="text-xs text-slate-400">{w.message}</p>
                          {w.affected_value && (
                            <p className="text-xs font-mono text-amber-400 mt-1">
                              Value: {Number(w.affected_value).toLocaleString()}
                            </p>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* ── STEP 4: EXPORT ── */}
        {step === "export" && (
          <div className="space-y-5 animate-in">
            <div>
              <h2 className="font-display text-2xl font-bold text-white">Export Data</h2>
              <p className="text-slate-400 mt-1">
                Download normalized usage data in your preferred format
              </p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {[
                {
                  format: "standard",
                  title: "Standard Format",
                  desc: "Schema-versioned JSON with full billing preview, line items, and metadata. Ready to import directly.",
                  icon: Zap,
                  color: "brand",
                  badge: "Recommended",
                },
                {
                  format: "csv",
                  title: "Normalized CSV",
                  desc: "Clean, normalized CSV with standardized column names. Import to any spreadsheet or BI tool.",
                  icon: Database,
                  color: "emerald",
                  badge: null,
                },
                {
                  format: "json",
                  title: "Raw JSON",
                  desc: "Simple JSON array of usage records. Easy to process programmatically or feed into other APIs.",
                  icon: FileText,
                  color: "blue",
                  badge: null,
                },
              ].map(opt => (
                <div key={opt.format} className="card hover:glow-sm transition-all">
                  <div className="flex items-start justify-between mb-3">
                    <div className={`w-10 h-10 rounded-xl flex items-center justify-center bg-${opt.color}-500/15`}>
                      <opt.icon size={18} className={`text-${opt.color}-400`} />
                    </div>
                    {opt.badge && <span className="badge-success">{opt.badge}</span>}
                  </div>
                  <h3 className="font-semibold text-white mb-1">{opt.title}</h3>
                  <p className="text-xs text-slate-500 mb-4 leading-relaxed">{opt.desc}</p>
                  <button
                    onClick={() => handleExport(opt.format)}
                    disabled={loading}
                    className="btn-primary w-full justify-center text-sm"
                  >
                    <Download size={14} />
                    Download {opt.format.toUpperCase()}
                  </button>
                </div>
              ))}
            </div>

            {/* Summary */}
            {previewResult && (
              <div className="card bg-brand-600/10 border-brand-500/20">
                <div className="flex items-center gap-3 mb-4">
                  <CheckCircle2 size={18} className="text-brand-400" />
                  <h3 className="font-semibold text-white">Pipeline Complete</h3>
                </div>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                  <div>
                    <div className="text-slate-500 text-xs mb-1">Job ID</div>
                    <code className="text-brand-300 font-mono text-xs">{jobId.slice(0, 12)}...</code>
                  </div>
                  <div>
                    <div className="text-slate-500 text-xs mb-1">Rows Processed</div>
                    <div className="font-semibold text-white">{ingestResult?.row_count.toLocaleString()}</div>
                  </div>
                  <div>
                    <div className="text-slate-500 text-xs mb-1">Invoice Total</div>
                    <div className="font-semibold text-white">${previewResult.preview.total.toFixed(2)}</div>
                  </div>
                  <div>
                    <div className="text-slate-500 text-xs mb-1">Warnings</div>
                    <div className={`font-semibold ${previewResult.warnings.length > 0 ? "text-amber-400" : "text-emerald-400"}`}>
                      {previewResult.warnings.length}
                    </div>
                  </div>
                </div>
              </div>
            )}

            <button onClick={reset} className="btn-secondary">
              <RefreshCw size={14} /> Process Another File
            </button>
          </div>
        )}
      </main>
    </div>
  );
}