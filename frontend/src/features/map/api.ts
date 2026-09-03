import { apiFetch } from "@/lib/api/client";

/**
 * Coordinates are strings on the wire, like amounts (ADR-003).
 *
 * A float latitude compared by equality is the same class of bug as a float
 * rupee — and store deduplication compares coordinates.
 */
export interface Store {
  id: string;
  name: string;
  address_line: string | null;
  pincode: string | null;
  city: string | null;
  latitude: string | null;
  longitude: string | null;
  source: string;
  confirmed: boolean;
}

export interface MapPin {
  store: Store;
  price_count: number;
  report_count: number;
  headline: string | null;
}

export interface ItemPrice {
  canonical_item_id: string;
  item_name: string;
  store: Store;
  median_price: string;
  min_price: string;
  max_price: string;
  contributors: number;
  last_seen_on: string;
  currency: string;
}

export interface Comment {
  id: string;
  body: string;
  upvote_count: number;
  created_at: string;
  mine: boolean;
  voted: boolean;
}

export interface StoreReport {
  id: string;
  store_id: string;
  kind: string;
  item_text: string;
  price: string | null;
  note: string | null;
  status: string;
  agree_count: number;
  dispute_count: number;
  upvote_count: number;
  promoted: boolean;
  created_at: string;
  mine: boolean;
  voted: boolean;
  /** `null` when this user has not answered yet, which reads differently from
   *  having answered "no". */
  verified_by_me: boolean | null;
  store_name: string | null;
}

export interface Bounds {
  minLat: number;
  maxLat: number;
  minLon: number;
  maxLon: number;
}

/** The backend refuses a viewport wider than two degrees, so callers zoom in. */
export const MAX_SPAN_DEGREES = 2;

export function fetchPins(bounds: Bounds): Promise<MapPin[]> {
  const params = new URLSearchParams({
    min_lat: bounds.minLat.toFixed(6),
    max_lat: bounds.maxLat.toFixed(6),
    min_lon: bounds.minLon.toFixed(6),
    max_lon: bounds.maxLon.toFixed(6),
  });
  return apiFetch(`/api/v1/pricegraph/map?${params}`);
}

export function fetchStore(storeId: string): Promise<Store> {
  return apiFetch(`/api/v1/pricegraph/stores/${storeId}`);
}

export function fetchStoreReports(storeId: string): Promise<StoreReport[]> {
  return apiFetch(`/api/v1/community/reports?store_id=${storeId}`);
}

export function pinStore(data: {
  name: string;
  latitude: string;
  longitude: string;
  pincode?: string;
  note?: string;
}): Promise<Store> {
  return apiFetch("/api/v1/pricegraph/stores/pin", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function searchItems(
  q: string,
): Promise<{ id: string; name: string; brand: string | null; observations: number }[]> {
  return apiFetch(`/api/v1/pricegraph/items/search?q=${encodeURIComponent(q)}`);
}

export function fetchItemPrices(itemId: string, pincode?: string): Promise<ItemPrice[]> {
  const query = pincode ? `?pincode=${pincode}` : "";
  return apiFetch(`/api/v1/pricegraph/items/${itemId}/prices${query}`);
}

// --- community writes -------------------------------------------------------
//
// These endpoints shipped in M17 with no caller anywhere, while the store sheet
// told users to "add it" and offered no control to. This is that half.

export type ReportKind = "unique_item" | "good_price";

export function createReport(data: {
  store_id: string;
  kind: ReportKind;
  item_text: string;
  price?: string;
  note?: string;
}): Promise<StoreReport> {
  return apiFetch("/api/v1/community/reports", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function verifyReport(reportId: string, agrees: boolean): Promise<unknown> {
  return apiFetch(`/api/v1/community/reports/${reportId}/verify`, {
    method: "POST",
    body: JSON.stringify({ agrees }),
  });
}

/** A toggle, because that is what the button does. Returns the new state. */
export function toggleVote(
  target: "report" | "comment",
  targetId: string,
): Promise<{ upvoted: boolean }> {
  return apiFetch(`/api/v1/community/${target}/${targetId}/vote`, { method: "POST" });
}

export function fetchComments(reportId: string): Promise<Comment[]> {
  return apiFetch(`/api/v1/community/reports/${reportId}/comments`);
}

export function addComment(reportId: string, body: string): Promise<Comment> {
  return apiFetch(`/api/v1/community/reports/${reportId}/comments`, {
    method: "POST",
    body: JSON.stringify({ body }),
  });
}

export function confirmStore(storeId: string): Promise<{ confirmed: boolean }> {
  return apiFetch(`/api/v1/pricegraph/stores/${storeId}/confirm`, { method: "POST" });
}

export interface CheaperElsewhere {
  item_name: string;
  your_price: string;
  best_price: string;
  saving: string;
  saving_percent: string;
  store: Store;
  contributors: number;
  last_seen_on: string;
  caveats: string[];
}

/** `null` when nowhere is cheaper, which is a real and common answer. */
export function fetchCheaper(
  itemId: string,
  paid: string,
  pincode?: string,
): Promise<CheaperElsewhere | null> {
  const params = new URLSearchParams({ paid });
  if (pincode) params.set("pincode", pincode);
  return apiFetch(`/api/v1/pricegraph/items/${itemId}/cheaper?${params}`);
}
