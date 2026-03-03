import axios from "axios";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export const api = axios.create({
  baseURL: API_URL,
  headers: { "Content-Type": "application/json" },
});

// ─── Types ────────────────────────────────────────────────────

export interface Customer {
  id: string;
  name: string;
  email: string;
  external_id?: string;
}

export interface PricingRule {
  id: string;
  metric_name: string;
  display_name: string;
  unit_label: string;
  pricing_model: string;
  base_price: number;
  free_tier_limit: number;
  tiers?: any;
}

export interface PricingPlan {
  id: string;
  name: string;
  description?: string;
  rules: PricingRule[];
}

export interface FieldMapping {
  id: string;
  source_field: string;
  target_field: string;
  confidence: number;
  mapping_method: string;
  is_confirmed: boolean;
}

export interface IngestResponse {
  job_id: string;
  status: string;
  row_count: number;
  columns_detected: string[];
  suggested_mappings: FieldMapping[];
  sample_rows: Record<string, any>[];
  message: string;
}

export interface BillingLineItem {
  metric_name: string;
  display_name: string;
  unit_label: string;
  total_quantity: number;
  billable_quantity: number;
  free_tier_used: number;
  pricing_model: string;
  unit_price?: number;
  amount: number;
  tiers_breakdown?: any[];
}

export interface BillingWarning {
  id: string;
  severity: "info" | "warning" | "critical";
  warning_type: string;
  message: string;
  metric_name?: string;
  affected_value?: number;
  expected_range_low?: number;
  expected_range_high?: number;
}

export interface BillingPreview {
  id: string;
  job_id: string;
  period_start: string;
  period_end: string;
  subtotal: number;
  total: number;
  line_items: BillingLineItem[];
  warnings: BillingWarning[];
  status: string;
  created_at: string;
}

export interface PreviewResponse {
  preview: BillingPreview;
  warnings: BillingWarning[];
  usage_summary: { metric_name: string; total: number }[];
}

// ─── API Methods ─────────────────────────────────────────────

export const apiClient = {
  // Customers
  getCustomers: () => api.get<Customer[]>("/customers"),

  // Plans
  getPricingPlans: () => api.get<PricingPlan[]>("/pricing-plans"),

  // Mock data
  getScenarios: () => api.get<{ scenarios: any[] }>("/mock-data/scenarios"),
  generateMockData: (customerId: string, scenario: string, numDays: number) =>
    api.post(
      "/mock-data/generate",
      { customer_id: customerId, scenario, num_days: numDays },
      { responseType: "blob" }
    ),

  // Ingestion
  ingestCSV: (file: File, customerId?: string) => {
    const form = new FormData();
    form.append("file", file);
    return api.post<IngestResponse>(
      `/ingest/csv${customerId ? `?customer_id=${customerId}` : ""}`,
      form,
      { headers: { "Content-Type": "multipart/form-data" } }
    );
  },

  ingestJSON: (file: File, customerId?: string) => {
    const form = new FormData();
    form.append("file", file);
    return api.post<IngestResponse>(
      `/ingest/json${customerId ? `?customer_id=${customerId}` : ""}`,
      form,
      { headers: { "Content-Type": "multipart/form-data" } }
    );
  },

  // Mappings
  getMappings: (jobId: string) => api.get<FieldMapping[]>(`/jobs/${jobId}/mappings`),
  updateMappings: (jobId: string, mappings: any[]) =>
    api.put(`/jobs/${jobId}/mappings`, { mappings }),

  // Normalize & Preview
  normalizeJob: (jobId: string) => api.post(`/jobs/${jobId}/normalize`),
  generatePreview: (jobId: string, planId?: string) =>
    api.post<PreviewResponse>(`/jobs/${jobId}/preview${planId ? `?plan_id=${planId}` : ""}`),

  // Export
  exportJob: (jobId: string, format: string) =>
    api.post(`/jobs/${jobId}/export?format=${format}`, {}, { responseType: "blob" }),

  // Jobs
  getJobs: (customerId?: string) =>
    api.get(`/jobs${customerId ? `?customer_id=${customerId}` : ""}`),
};

// Helper to download blob
export function downloadBlob(blob: Blob, filename: string) {
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  window.URL.revokeObjectURL(url);
}