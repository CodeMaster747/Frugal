/**
 * One place every react-query key is spelled.
 *
 * Keys were inline arrays literal-typed at each call site: `["accounts"]`
 * appeared in five files and `["insights"]` was invalidated from three. That
 * works right up until one of them is spelled differently -- and the failure is
 * silent, because an invalidation that matches no key is not an error. It just
 * leaves stale data on screen.
 *
 * `as const` on every return is what makes the tuples literal types, so
 * `useQuery` still narrows exactly as it did with an inline array.
 *
 * Convention: a bare key is the whole collection, and a key with arguments is a
 * subset of it. `keys.transactions.all` therefore invalidates every filtered
 * list, which is what a mutation almost always wants.
 */

export const keys = {
  system: {
    readiness: () => ["system", "readiness"] as const,
    providers: () => ["provider-status"] as const,
  },

  finance: {
    accounts: () => ["accounts"] as const,
    categories: () => ["categories"] as const,
    transactions: {
      all: () => ["transactions"] as const,
      list: (filters: { q: string; reviewOnly: boolean }) => ["transactions", filters] as const,
    },
    budgets: () => ["budgets"] as const,
    goals: () => ["goals"] as const,
    recurring: () => ["recurring"] as const,
  },

  analytics: {
    dashboard: () => ["analytics", "dashboard"] as const,
    savingsRate: () => ["analytics", "savings-rate"] as const,
  },

  health: {
    score: () => ["health-score"] as const,
    rubric: () => ["health-rubric"] as const,
    insights: () => ["insights"] as const,
  },

  forecast: {
    horizon: (days: number) => ["forecast", days] as const,
  },

  advisor: {
    search: (query: string) => ["advisor-search", query] as const,
  },

  simulator: {
    templates: () => ["scenario-templates"] as const,
  },

  notifications: {
    feed: () => ["notifications"] as const,
    preferences: () => ["notification-preferences"] as const,
  },

  market: {
    wishlist: () => ["wishlist"] as const,
    alerts: () => ["price-alerts"] as const,
    product: (id: string) => ["product-detail", id] as const,
    search: (query: string) => ["watchlist-search", query] as const,
    offers: (query: string, askingPrice: string) => ["offers", query, askingPrice] as const,
    reliabilityRubric: () => ["reliability-rubric"] as const,
  },

  receipts: {
    all: () => ["receipts"] as const,
    one: (id: string) => ["receipt", id] as const,
    image: (id: string) => ["receipt-image", id] as const,
  },

  sms: {
    queue: () => ["sms-queue"] as const,
  },

  map: {
    pins: (bounds: { minLat: number; maxLat: number; minLon: number; maxLon: number }) =>
      // Rounded to three decimals -- about 100 m. Without it every pixel of pan
      // is a distinct key and the cache never hits.
      [
        "map-pins",
        bounds.minLat.toFixed(3),
        bounds.maxLat.toFixed(3),
        bounds.minLon.toFixed(3),
        bounds.maxLon.toFixed(3),
      ] as const,
    store: (id: string) => ["store", id] as const,
    itemSearch: (q: string) => ["item-search", q] as const,
    itemPrices: (id: string, pincode?: string) => ["item-prices", id, pincode ?? ""] as const,
  },

  community: {
    reports: (storeId: string) => ["store-reports", storeId] as const,
    myReports: () => ["my-reports"] as const,
    myComments: () => ["my-comments"] as const,
    comments: (reportId: string) => ["report-comments", reportId] as const,
    trust: () => ["my-trust"] as const,
    mergeCandidates: () => ["merge-candidates"] as const,
  },

  points: {
    balance: () => ["points-balance"] as const,
    ledger: () => ["points-ledger"] as const,
    rewards: () => ["rewards"] as const,
  },

  pricegraph: {
    contributions: () => ["my-contributions"] as const,
  },
} as const;
