import { useEffect, useRef, useState } from "react";
import { Layout } from "@/components/Layout";
import { ConfirmDeleteModal } from "@/components/ConfirmDeleteModal";
import { useAuth } from "@/context/AuthContext";
import { useIsMobile } from "@/hooks/use-mobile";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  AlertCircle,
  AlertTriangle,
  Bot,
  Download,
  File,
  FileSpreadsheet,
  FileText,
  FileType,
  Heart,
  Info,
  Loader2,
  MapPin,
  MessageSquare,
  Mic,
  MicOff,
  Navigation,
  PanelLeftClose,
  PanelLeftOpen,
  Paperclip,
  Pencil,
  Plus,
  Presentation,
  Send,
  ShieldAlert,
  Sparkles,
  Square,
  Star,
  Trash2,
  User,
  Volume2,
  VolumeX,
  X,
  Zap,
} from "lucide-react";

// Lazily loads the Google Maps JavaScript API exactly once, no matter how
// many LocationConfirmMap instances mount over the life of a chat (every
// hospital_confirm message gets its own) -- a second injected
// maps/api/js script tag logs "google.maps already loaded" console
// errors, so the load Promise is cached at module scope instead of
// per-component.
let _googleMapsLoadPromise: Promise<void> | null = null;
function loadGoogleMaps(): Promise<void> {
  const google = (window as unknown as { google?: { maps?: unknown } }).google;
  if (google?.maps) return Promise.resolve();
  if (_googleMapsLoadPromise) return _googleMapsLoadPromise;
  _googleMapsLoadPromise = new Promise((resolve, reject) => {
    const apiKey = import.meta.env.VITE_GOOGLE_MAPS_API_KEY as string | undefined;
    if (!apiKey) {
      reject(new Error("VITE_GOOGLE_MAPS_API_KEY is not set"));
      return;
    }
    const script = document.createElement("script");
    script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(apiKey)}`;
    script.async = true;
    script.defer = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("Failed to load the Google Maps script"));
    document.head.appendChild(script);
  });
  return _googleMapsLoadPromise;
}

const BACKEND = import.meta.env.VITE_BACKEND_URL || "http://localhost:5001";
const MAX_INPUT_CHARS = 500;
const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024; // 10 MB
// Additive: keeps existing pdf/doc/docx/csv/txt support, adds xlsx/pptx.
// Images (jpg/jpeg/png) were removed from attachment support.
const ALLOWED_ATTACHMENT_EXTENSIONS = new Set([
  ".pdf",
  ".doc",
  ".docx",
  ".csv",
  ".txt",
  ".xlsx",
  ".pptx",
]);
const ATTACHMENT_ACCEPT = Array.from(ALLOWED_ATTACHMENT_EXTENSIONS).join(",");
const TTS_ENABLED_STORAGE_KEY = "aiAssistant.ttsEnabled";

// Voice input (mic) is transcribed by Groq's cloud Whisper API (see
// backend /api/asr -> speech_service.py), which natively supports every
// reply language this app offers.
const ASR_SUPPORTED_LANGUAGES = new Set(["en", "hi", "kn", "ta", "te"]);

// BCP-47 tags for the browser's built-in speechSynthesis (client-side TTS,
// see speakText/speakUtterance below) -- matches the same 5 languages ASR
// and the reply-language dropdown use.
const SPEECH_SYNTHESIS_LANG_CODES: Record<string, string> = {
  en: "en-US",
  hi: "hi-IN",
  kn: "kn-IN",
  ta: "ta-IN",
  te: "te-IN",
};
const getLanguageCode = (language: string): string =>
  SPEECH_SYNTHESIS_LANG_CODES[language] || "en-US";

// A recording stopped before this many ms in has too few (or zero) actual
// audio frames for the backend decoder to read -- it fails with a raw
// "End of file" decoder error. Reject it client-side instead of round-
// tripping to the server for a recording that can never transcribe.
const MIN_RECORDING_MS = 400;

const makeSessionId = () => {
  try {
    if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  } catch {
    // Fall through to the legacy generator.
  }
  return `web-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
};

interface Message {
  role: "user" | "assistant";
  text: string;
  ts: number;
  kind?:
    | "normal"
    | "guardrail"
    | "emergency"
    | "error"
    | "stopped"
    | "hospital_offer"
    | "hospital_confirm"
    | "hospital_results"
    | "hospital_fallback";
  cached?: boolean;
  fileName?: string;
  modelTier?: "primary" | "fallback" | "portkey" | "kb_only" | "failed";
  // hospital_offer: the high-risk-file quick-reply message (Trigger 2).
  riskPercent?: number;
  offerHandled?: boolean; // quick-reply clicked -- hide the button afterward
  // hospital_confirm: the map-preview step shown BEFORE any hospital search
  // runs -- the browser's raw geolocation result lands here first so the
  // user can visually confirm/correct it (drag the pin or type a place
  // name) instead of the app blindly trusting the browser's own,
  // unverified `accuracy` estimate.
  pendingLat?: number;
  pendingLng?: number;
  confirmed?: boolean; // "Search from here" clicked -- hide the map afterward
  // hospital_results: hospital cards (both triggers render through this).
  hospitals?: HospitalResult[];
  hospitalUrgent?: boolean;
  locationAccuracyMeters?: number;
}

interface HospitalResult {
  name: string;
  address: string;
  rating: number | null;
  open_now: boolean | null;
  // Overpass/OSM (unlike Places) returns straight-line distance for free;
  // rating/open_now are always null since OSM data doesn't carry them.
  distance_meters?: number | null;
  lat: number | null;
  lng: number | null;
  maps_url: string;
}

interface PendingAttachment {
  file: File;
  name: string;
  size: number;
  fileId?: string;
}

const formatBytes = (bytes: number) => {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${bytes} B`;
};

const fileIconFor = (name: string) => {
  const ext = name.includes(".") ? name.slice(name.lastIndexOf(".")).toLowerCase() : "";
  if (ext === ".pdf") return <FileText className="h-3.5 w-3.5" />;
  if (ext === ".doc" || ext === ".docx") return <FileType className="h-3.5 w-3.5" />;
  if (ext === ".csv" || ext === ".xlsx") return <FileSpreadsheet className="h-3.5 w-3.5" />;
  if (ext === ".pptx") return <Presentation className="h-3.5 w-3.5" />;
  return <File className="h-3.5 w-3.5" />;
};

const fileColorFor = (name: string) => {
  const ext = name.includes(".") ? name.slice(name.lastIndexOf(".")).toLowerCase() : "";
  if (ext === ".pdf") return "bg-red-50 text-red-600 border-red-200";
  if (ext === ".doc" || ext === ".docx") return "bg-blue-50 text-blue-600 border-blue-200";
  if (ext === ".csv" || ext === ".xlsx") return "bg-green-50 text-green-600 border-green-200";
  if (ext === ".pptx") return "bg-orange-50 text-orange-600 border-orange-200";
  return "bg-slate-100 text-slate-600 border-slate-200";
};

function FileChip({
  name,
  size,
  onRemove,
}: {
  name: string;
  size?: number;
  onRemove?: () => void;
}) {
  return (
    <span
      className={`inline-flex max-w-full items-center gap-1.5 rounded-lg border px-2 py-1 text-xs font-medium ${fileColorFor(
        name
      )}`}
    >
      {fileIconFor(name)}
      <span className="max-w-[220px] truncate">{name}</span>
      {size !== undefined && <span className="opacity-70">· {formatBytes(size)}</span>}
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove attachment ${name}`}
          className="ml-0.5 rounded p-0.5 opacity-60 transition-opacity hover:bg-black/10 hover:opacity-100"
        >
          <X className="h-3 w-3" />
        </button>
      )}
    </span>
  );
}

interface ConversationItem {
  id: string;
  session_id: string;
  user_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count?: number;
}

const SUGGESTIONS = ["When should I go to the hospital?"];

const WELCOME: Message = {
  role: "assistant",
  text: "Hi! I am Rural Healthcare AI. I am here to help you related to predicting diseases and answering your healthcare questions. Feel free to ask whatever you want related to healthcare!",
  ts: Date.now(),
  kind: "normal",
};

const formatMessage = (text: string) => {
  const trimmed = text.trim();
  if (!trimmed) return [];
  return trimmed.split(/\n+/).filter(Boolean);
};

// Generic fallback search link when we can't get the user's precise
// location -- Google Maps' own "hospitals near me" resolves using the
// browser's own location prompt, without this app ever seeing it.
const GENERIC_HOSPITALS_MAPS_URL =
  "https://www.google.com/maps/search/?api=1&query=hospitals+near+me";

// Distance labels shown on hospital cards come straight from the backend's
// Overpass-based search, computed server-side from the exact coordinates we
// send it -- so a wrong label always traces back to a wrong *input* coordinate, not a mismatched
// calculation. Above this accuracy (meters), the browser's estimate is WiFi/
// IP-based rather than GPS and can be off by tens of km -- worth telling the
// user rather than silently showing hospitals that "look" nearby but aren't.
const LOW_ACCURACY_THRESHOLD_METERS = 2000;

// Requests the browser's geolocation. Never called automatically -- only
// from the hospital-search flow, itself only reachable by the user asking
// for nearby hospitals or clicking the "Yes, find hospitals" quick-reply.
const getUserLocation = (): Promise<{ lat: number; lng: number; accuracy: number }> => {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error("Location is not supported on this device."));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) =>
        resolve({
          lat: pos.coords.latitude,
          lng: pos.coords.longitude,
          accuracy: pos.coords.accuracy,
        }),
      (err) => reject(err),
      {
        enableHighAccuracy: true, // request GPS over WiFi/IP-based estimation
        timeout: 15000,
        maximumAge: 0, // never reuse a stale/cached position
      },
    );
  });
};

// ============================================================
// LOCATION-CONFIRMATION MAP -- the actual fix for untrustworthy
// browser-reported geolocation accuracy.
//
// pos.coords.accuracy is the device/OS's OWN confidence estimate for its
// fix -- it is never independently verified. On a desktop/laptop with no
// GPS chip, the browser silently falls back to WiFi- or IP-based
// positioning, and in areas with sparse WiFi-location data (rural/semi-
// rural -- this app's actual target users) that fallback can be tens of km
// off while still reporting a deceptively small `accuracy` number. So this
// app no longer runs a hospital search directly off the raw geolocation
// result: it shows the detected point on a Google Map with a draggable pin
// first and lets the user visually confirm or correct it (or type a city/
// area name to re-center instead) BEFORE any search happens. Whatever
// lat/lng the pin sits at when "Search from here" is clicked is what
// actually gets searched -- never the raw, unconfirmed coordinate.
//
// This is the map *widget* only (Google Maps JS API, via VITE_GOOGLE_MAPS_
// API_KEY) -- the actual hospital search stays on Overpass and address
// search stays on Nominatim, both server-side; nothing here calls Google
// Places or Google Geocoding.
// ============================================================
function LocationConfirmMap({
  initialLat,
  initialLng,
  accuracyMeters,
  onConfirm,
}: {
  initialLat: number;
  initialLng: number;
  accuracyMeters: number;
  onConfirm: (lat: number, lng: number) => void;
}) {
  const mapDivRef = useRef<HTMLDivElement | null>(null);
  // Untyped (no @types/google.maps dependency) -- these just hold whatever
  // loadGoogleMaps() hands back at runtime.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const mapRef = useRef<any>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const markerRef = useRef<any>(null);
  const resizeObserverRef = useRef<ResizeObserver | null>(null);
  const [pin, setPin] = useState({ lat: initialLat, lng: initialLng });
  const [placeText, setPlaceText] = useState("");
  const [geocoding, setGeocoding] = useState(false);
  const [geocodeError, setGeocodeError] = useState<string | null>(null);
  const [mapLoadError, setMapLoadError] = useState<string | null>(null);

  // Mounts the Google Map exactly once per message (this component is only
  // ever rendered for one hospital_confirm message at a time).
  useEffect(() => {
    let cancelled = false;
    if (!mapDivRef.current || mapRef.current) return;

    loadGoogleMaps()
      .then(() => {
        if (cancelled || !mapDivRef.current || mapRef.current) return;
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const google = (window as any).google;
        const map = new google.maps.Map(mapDivRef.current, {
          center: { lat: initialLat, lng: initialLng },
          zoom: 13,
          scrollwheel: false,
          streetViewControl: false,
          mapTypeControl: false,
          fullscreenControl: false,
        });
        const marker = new google.maps.Marker({
          position: { lat: initialLat, lng: initialLng },
          map,
          draggable: true,
        });
        marker.addListener("dragend", () => {
          const pos = marker.getPosition();
          if (!pos) return;
          setPin({ lat: pos.lat(), lng: pos.lng() });
        });
        mapRef.current = map;
        markerRef.current = marker;

        // Same layout-timing issue as before (this component mounts inside
        // a scrolling chat message list, where the container can still be
        // mid-layout at map-creation time): force Google Maps to recompute
        // its viewport once the container has settled into its real size,
        // and again on any later resize (sidebar toggle, window resize).
        // `resize` recentres the map on its container's midpoint by
        // default, so the explicit setCenter() after it is what keeps the
        // pin's actual coordinate on-screen rather than drifting off.
        const recenterOnResize = () => {
          google.maps.event.trigger(map, "resize");
          const pos = marker.getPosition();
          map.setCenter(pos ?? { lat: initialLat, lng: initialLng });
        };
        requestAnimationFrame(recenterOnResize);
        const resizeObserver = new ResizeObserver(recenterOnResize);
        resizeObserver.observe(mapDivRef.current);
        resizeObserverRef.current = resizeObserver;
      })
      .catch((err) => {
        console.error("[hospital map] Google Maps failed to load:", err);
        if (!cancelled) {
          setMapLoadError("Could not load the map. You can still search by area name below.");
        }
      });

    return () => {
      cancelled = true;
      resizeObserverRef.current?.disconnect();
      resizeObserverRef.current = null;
      mapRef.current = null;
      markerRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const recenter = (lat: number, lng: number) => {
    setPin({ lat, lng });
    markerRef.current?.setPosition({ lat, lng });
    mapRef.current?.setCenter({ lat, lng });
    mapRef.current?.setZoom(14);
  };

  // Geocodes a typed place name via the backend (Nominatim, same provider
  // as the coordinate search -- see hospital_search.geocode_place) and just
  // moves the pin there. Does NOT search yet -- the user still confirms
  // with "Search from here" afterward, same as a dragged pin.
  const handleGeocode = async (e: React.FormEvent) => {
    e.preventDefault();
    const q = placeText.trim();
    if (!q || geocoding) return;
    setGeocoding(true);
    setGeocodeError(null);
    try {
      const resp = await fetch(`${BACKEND}/hospitals/geocode`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ place: q }),
      });
      const json: { lat?: number; lng?: number; error?: string } = await resp
        .json()
        .catch(() => ({ error: "Could not look that up. Please try again." }));
      if (!resp.ok || json.error || json.lat == null || json.lng == null) {
        setGeocodeError(json.error || "Could not find that place. Try a nearby city or landmark.");
        return;
      }
      recenter(json.lat, json.lng);
    } catch (err) {
      // Logged so a silent client-side failure (network error, thrown
      // exception before the fetch even completes) is visible in the
      // browser console instead of just "nothing happened" -- see the
      // block comment above the fetch call for why this can't be a
      // backend issue if this branch is what's firing.
      console.error("[hospital geocode] Find failed:", err);
      setGeocodeError("Network error looking that up. Please try again.");
    } finally {
      setGeocoding(false);
    }
  };

  // The browser's accuracy number is a hint, not a guarantee (see block
  // comment above) -- above this threshold we say so explicitly and point
  // at the map/pin as the actual safeguard, rather than a bare number.
  const lowAccuracy = accuracyMeters > LOW_ACCURACY_THRESHOLD_METERS;

  return (
    <div className="mt-2 space-y-2">
      {lowAccuracy && (
        <div className="flex items-start gap-2 rounded-xl border border-amber-300 bg-amber-50 p-2.5 text-xs font-medium text-amber-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            We couldn't get a precise GPS lock. Please confirm your location on the map below
            before searching.
          </span>
        </div>
      )}
      <p className="text-xs text-slate-500">
        We detected your location as approximately here. If this looks wrong, drag the pin to
        your actual location, or search by area name instead.
      </p>
      <div
        ref={mapDivRef}
        className="h-48 w-full overflow-hidden rounded-xl border border-emerald-200"
      />
      {mapLoadError && <p className="text-xs font-medium text-red-600">{mapLoadError}</p>}
      <form onSubmit={handleGeocode} className="flex items-center gap-1.5">
        <input
          type="text"
          value={placeText}
          onChange={(e) => setPlaceText(e.target.value)}
          placeholder="Or type your city/area name"
          aria-label="City or area to re-center the map"
          className="min-w-0 flex-1 rounded-full border border-emerald-200 bg-white px-3 py-1.5 text-xs text-slate-700 outline-none focus:border-emerald-400"
        />
        <button
          type="submit"
          disabled={geocoding || !placeText.trim()}
          className="inline-flex shrink-0 items-center gap-1 rounded-full border border-emerald-300 bg-white px-3 py-1.5 text-xs font-semibold text-emerald-700 shadow-sm transition-colors hover:bg-emerald-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {geocoding ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <MapPin className="h-3.5 w-3.5" />
          )}
          Find
        </button>
      </form>
      {geocodeError && <p className="text-xs font-medium text-red-600">{geocodeError}</p>}
      <button
        type="button"
        onClick={() => onConfirm(pin.lat, pin.lng)}
        className="inline-flex items-center gap-1.5 rounded-full bg-emerald-600 px-4 py-2 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-emerald-700"
      >
        <Navigation className="h-3.5 w-3.5" /> Search from here
      </button>
    </div>
  );
}

// A single hospital result card -- styled to match the app's existing
// teal/emerald result-card pattern (e.g. the assessment result cards).
function HospitalCard({ hospital }: { hospital: HospitalResult }) {
  return (
    <div className="rounded-2xl border border-emerald-200 bg-white p-3 shadow-sm">
      <div className="flex items-start gap-2">
        <MapPin className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-slate-800">{hospital.name}</p>
          {hospital.address && <p className="mt-0.5 text-xs text-slate-500">{hospital.address}</p>}
          <div className="mt-1 flex flex-wrap items-center gap-2">
            {hospital.rating != null && (
              <span className="inline-flex items-center gap-1 text-xs font-medium text-amber-600">
                <Star className="h-3 w-3 fill-current" /> {hospital.rating}
              </span>
            )}
            {hospital.open_now === true && (
              <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-semibold text-emerald-700">
                Open now
              </span>
            )}
            {hospital.open_now === false && (
              <span className="rounded-full bg-red-50 px-2 py-0.5 text-[10px] font-semibold text-red-600">
                Closed
              </span>
            )}
            {hospital.distance_meters != null && (
              <span className="text-xs font-medium text-slate-400">
                {hospital.distance_meters >= 1000
                  ? `${(hospital.distance_meters / 1000).toFixed(1)} km away`
                  : `${hospital.distance_meters} m away`}
              </span>
            )}
          </div>
        </div>
      </div>
      <a
        href={hospital.maps_url}
        target="_blank"
        rel="noopener noreferrer"
        className="mt-2 inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs font-semibold text-emerald-700 transition-colors hover:bg-emerald-100"
      >
        <Navigation className="h-3.5 w-3.5" /> Get Directions
      </a>
    </div>
  );
}

export default function AIAssistant() {
  const [messages, setMessages] = useState<Message[]>([WELCOME]);
  const [inputText, setInputText] = useState("");
  const [loading, setLoading] = useState(false);
  const [responseCount, setResponseCount] = useState(0);
  const [sessionId, setSessionId] = useState(makeSessionId());
  const [userEmail, setUserEmail] = useState<string | null>(null);
  const [sessionStartedAt, setSessionStartedAt] = useState<number>(Date.now());

  // File attachment (pdf/doc/docx/csv/txt, max 10 MB, one per message).
  const [attachment, setAttachment] = useState<PendingAttachment | null>(null);
  const [attachmentError, setAttachmentError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Lets the user interrupt an in-flight /ai-chat request (slow LLM tiers
  // can take up to a minute) and immediately send a new message instead of
  // waiting it out.
  const chatAbortControllerRef = useRef<AbortController | null>(null);

  const { user } = useAuth();
  const isMobile = useIsMobile();
  const supabaseUserId = user?.id ?? null;

  const [conversations, setConversations] = useState<ConversationItem[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState<boolean>(() => {
    if (typeof window === "undefined") return true;
    return window.innerWidth >= 768;
  });
  const [deleteTarget, setDeleteTarget] = useState<ConversationItem | null>(null);
  const printRef = useRef<HTMLDivElement>(null);

  // Inline rename of a saved conversation's title.
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const renameInputRef = useRef<HTMLInputElement | null>(null);
  const cancelRenameRef = useRef(false);

  useEffect(() => {
    if (isMobile) setSidebarOpen(false);
  }, [isMobile]);

  // Response count within the current 30-response/5h window (see
  // RESPONSE_LIMIT_PER_WINDOW in app.py) -- just displayed here; the
  // cooldown popup (cooldownUntilIso) is what actually blocks further use.
  useEffect(() => {
    try {
      const raw = localStorage.getItem("user");
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed.email === "string" && parsed.email) {
          setUserEmail(parsed.email);
        }
      }
    } catch {
      // No stored user; stay anonymous.
    }
  }, []);

  const refreshConversations = () => {
    if (!supabaseUserId) return;
    const query = new URLSearchParams();
    query.set("user_id", supabaseUserId);
    if (effectiveEmail) query.set("user_email", effectiveEmail);
    fetch(`${BACKEND}/api/conversations?${query.toString()}`)
      .then((r) => r.json())
      .then((d) => {
        if (d && d.success) {
          setConversations(d.conversations ?? []);
          if (typeof d.response_count === "number") setResponseCount(d.response_count);
        }
      })
      .catch(() => {
        // Sidebar refresh is best-effort; chat still works.
      });
  };

  useEffect(() => {
    refreshConversations();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [supabaseUserId]);

  const effectiveEmail = (user?.email as string | undefined) || userEmail || undefined;

  const openConversation = async (conv: ConversationItem) => {
    if (loading) return;
    try {
      const query = new URLSearchParams();
      query.set("user_id", supabaseUserId ?? "");
      if (effectiveEmail) query.set("user_email", effectiveEmail);
      const resp = await fetch(
        `${BACKEND}/api/conversations/${encodeURIComponent(conv.session_id)}/messages?${query.toString()}`
      );
      const json = await resp.json();
      if (!json || !json.success) return;
      const loaded: Message[] = (json.messages ?? []).map(
        (m: { sender: string; message_text: string; timestamp: string; kind?: string }) => ({
          role: m.sender === "user" ? "user" : "assistant",
          text: m.message_text,
          ts: new Date(m.timestamp).getTime(),
          kind:
            m.kind && m.kind !== "normal"
              ? (m.kind as Message["kind"])
              : undefined,
        })
      );
      stopSpeaking();
      removeAttachment();
      setMessages(loaded.length ? loaded : [WELCOME]);
      setInputText("");
      setSessionId(conv.session_id);
      setActiveConversationId(conv.session_id);
      setSessionStartedAt(new Date(conv.created_at || Date.now()).getTime());
      setResponseCount(json.response_count ?? responseCount);
      if (isMobile) setSidebarOpen(false);
    } catch {
      // Keep the current chat view if loading a past conversation fails.
    }
  };

  const handleDeleteConversation = async (conv: ConversationItem) => {
    setDeleteTarget(null);
    if (!supabaseUserId) return;
    try {
      await fetch(
        `${BACKEND}/api/conversations/${encodeURIComponent(conv.session_id)}?user_id=${encodeURIComponent(
          supabaseUserId
        )}`,
        { method: "DELETE" }
      );
      setConversations((prev) => prev.filter((c) => c.session_id !== conv.session_id));
      if (activeConversationId === conv.session_id) {
        void reset();
      }
    } catch {
      // Keep the list as-is if the delete request fails.
    }
  };

  const startRename = (conv: ConversationItem) => {
    setRenameDraft(conv.title || "");
    setRenamingId(conv.session_id);
  };

  const saveRename = async (conv: ConversationItem) => {
    setRenamingId(null);
    if (!supabaseUserId) return;
    const title = renameDraft.trim();
    if (!title) return; // Empty title: keep the auto-generated one.
    try {
      const resp = await fetch(
        `${BACKEND}/api/conversations/${encodeURIComponent(conv.session_id)}?user_id=${encodeURIComponent(
          supabaseUserId
        )}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title }),
        }
      );
      const json = await resp.json();
      if (json && json.success) {
        setConversations((prev) =>
          prev.map((c) =>
            c.session_id === conv.session_id
              ? { ...c, title: json.conversation?.title ?? title }
              : c
          )
        );
      }
    } catch {
      // Rename failed; keep the previous title.
    }
  };

  const formatConversationTime = (iso: string) => {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    const now = new Date();
    const sameDay = date.toDateString() === now.toDateString();
    return sameDay
      ? date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : date.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
  };

  const handleDownloadPdf = () => {
    stopSpeaking();
    window.setTimeout(() => window.print(), 50);
  };

  // Voice input (speech-to-text): recorded in-browser and transcribed by
  // Groq's cloud Whisper API via POST /api/asr (backend/speech_service.py) --
  // no local model, so this works for every reply language.
  const [listening, setListening] = useState(false);
  const [asrBusy, setAsrBusy] = useState(false);
  const [micError, setMicError] = useState("");

  // Text-to-speech: the browser's own built-in speechSynthesis, entirely
  // client-side -- no backend call, no memory cost on the server.
  // ttsEnabled controls AUTOPLAY of new incoming replies only (persisted so it
  // survives reloads); it never gates the per-message speaker buttons below.
  const [ttsEnabled, setTtsEnabled] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    try {
      return window.localStorage.getItem(TTS_ENABLED_STORAGE_KEY) === "true";
    } catch {
      return false;
    }
  });
  // Per-message typed city/area name for the GPS-free hospital-search
  // fallback (keyed by message.ts so multiple fallback prompts in one
  // conversation each keep their own draft text).
  const [placeQueries, setPlaceQueries] = useState<Record<number, string>>({});
  // Set when the backend reports the 30-response/5h cooldown is active;
  // holds the ISO timestamp it ends at, driving the popup dialog below.
  const [cooldownUntilIso, setCooldownUntilIso] = useState<string | null>(null);
  // Shows the "/help" guide popup -- set true whenever the user sends the
  // /help command (handled entirely client-side, see sendMessage). Also
  // available manually via the Info button next to the language selector.
  const [showHelpDialog, setShowHelpDialog] = useState(false);
  const [ttsLanguage, setTtsLanguage] = useState("en");
  const [languages, setLanguages] = useState<Record<string, string>>({ en: "English" });
  // speakingTs: message.ts whose audio is currently PLAYING (shows the stop icon).
  // loadingTs: message.ts currently fetching the cloud-TTS fallback (see
  // playBackendAudio below) -- the browser-voice path is instant and never
  // sets this.
  const [speakingTs, setSpeakingTs] = useState<number | null>(null);
  const [loadingTs, setLoadingTs] = useState<number | null>(null);
  // ttsError: last playback failure, shown to the user instead of silently
  // doing nothing.
  const [ttsError, setTtsError] = useState("");
  // blockedTs: a message whose fallback audio is fetched/loaded but whose
  // play() call was refused by the browser (autoplay policy). The SAME Audio
  // object stays in audioRef so a follow-up click can retry play()
  // synchronously, inside the click handler -- that's what lets the retry
  // succeed where the first (post-await) attempt didn't.
  const [blockedTs, setBlockedTs] = useState<number | null>(null);

  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  // Holds the <audio> element for the gTTS cloud-fallback path only -- the
  // browser-voice path never touches this.
  const audioRef = useRef<HTMLAudioElement | null>(null);

  // Cache of window.speechSynthesis.getVoices(): populated immediately and
  // refreshed on the "voiceschanged" event, since Chrome loads the voice
  // list asynchronously (it's often empty on the very first call).
  const speechVoicesRef = useRef<SpeechSynthesisVoice[]>([]);

  // Monotonic token that invalidates any in-flight TTS work (a queued
  // utterance) whenever speaking is stopped or a new message is clicked, so
  // only the most recent speakText() call can ever produce audio.
  const ttsTokenRef = useRef(0);

  // Auto-play (ttsEnabled) queue: speaking one message can outlast the next
  // reply arriving, so auto-play messages are queued (not spoken directly)
  // to make sure every reply's audio plays in order instead of the newest
  // reply cancelling whatever the previous one was still saying.
  const autoQueueRef = useRef<{ text: string; ts: number }[]>([]);
  const autoQueueBusyRef = useRef(false);

  const resetHeight = () => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "44px";
    textarea.style.overflowY = "hidden";
  };

  const autoResize = (textarea: HTMLTextAreaElement) => {
    textarea.style.height = "44px";
    const nextHeight = Math.min(textarea.scrollHeight, 120);
    textarea.style.height = `${nextHeight}px`;
    textarea.style.overflowY = textarea.scrollHeight > 120 ? "auto" : "hidden";
  };

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    autoResize(textarea);
  }, [inputText]);

  useEffect(() => {
    fetch(`${BACKEND}/api/tts/voices`)
      .then((r) => r.json())
      .then((d) => {
        if (d && typeof d.languages === "object" && Object.keys(d.languages).length) {
          setLanguages(d.languages);
        }
      })
      .catch(() => {
        // Language list is optional; fall back to the default language.
      });
  }, []);

  useEffect(() => {
    return () => {
      stopSpeaking();
      discardRecording();
    };
  }, []);

  // Chrome (and some other browsers) load the speechSynthesis voice list
  // asynchronously -- it's often empty on the very first call, populated
  // moments later via the "voiceschanged" event. Cache it eagerly and keep
  // it fresh so findVoiceForLanguage() below has real data to check against.
  useEffect(() => {
    if (!("speechSynthesis" in window)) return;
    const loadVoices = () => {
      speechVoicesRef.current = window.speechSynthesis.getVoices();
    };
    loadVoices();
    window.speechSynthesis.onvoiceschanged = loadVoices;
    return () => {
      window.speechSynthesis.onvoiceschanged = null;
    };
  }, []);

  // Persist the header Audio toggle across sessions (autoplay preference only).
  useEffect(() => {
    try {
      window.localStorage.setItem(TTS_ENABLED_STORAGE_KEY, String(ttsEnabled));
    } catch {
      // Best-effort; toggle still works for this session if storage is unavailable.
    }
  }, [ttsEnabled]);

  const stopSpeaking = () => {
    // Bumping the token invalidates ANY in-flight utterance/fetch from a
    // previous speakText() call -- this is what lets a click on message #4
    // abort message #1's still-loading/still-playing audio.
    ttsTokenRef.current += 1;
    // A manual interrupt also abandons any still-pending auto-play messages
    // rather than have them start playing right after.
    autoQueueRef.current = [];
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
      audioRef.current = null;
    }
    setSpeakingTs(null);
    setLoadingTs(null);
    setBlockedTs(null);
    setTtsError("");
  };

  // Finds a system voice matching `langCode` (exact match first, then just
  // the base language e.g. "hi" for "hi-IN"). Returns null both when no
  // matching voice exists AND when the voice list hasn't loaded yet -- the
  // caller only treats it as "unavailable" once speechVoicesRef is known to
  // be populated (see hasKnownVoiceGap below).
  const findVoiceForLanguage = (langCode: string): SpeechSynthesisVoice | null => {
    const voices = speechVoicesRef.current;
    if (!voices.length) return null;
    const lower = langCode.toLowerCase();
    return (
      voices.find((v) => v.lang.toLowerCase() === lower) ||
      voices.find((v) => v.lang.toLowerCase().startsWith(lower.split("-")[0])) ||
      null
    );
  };

  // True only when the voice list is known (non-empty) AND definitively has
  // no match -- distinguishes "genuinely unsupported" from "hasn't loaded
  // yet", so the cloud fallback below only fires on a real gap, not a
  // still-loading voice list.
  const hasKnownVoiceGap = (langCode: string): boolean =>
    speechVoicesRef.current.length > 0 && !findVoiceForLanguage(langCode);

  // Cloud TTS fallback (backend /api/tts -> gTTS, see tts_service.py) for
  // when the browser has no installed voice for `ttsLanguage` -- commonly
  // Kannada/Tamil/Telugu on Windows Chrome/Edge, which ship English (and
  // often Hindi) voices but not those three. No local model on the backend
  // either (gTTS is a plain cloud HTTPS call), so this stays within the
  // same "no local memory" constraint as the browser-voice path.
  const playBackendAudio = (text: string, ts: number, token: number): Promise<void> =>
    new Promise((resolve) => {
      (async () => {
        setLoadingTs(ts);
        let url: string | null = null;
        try {
          const resp = await fetch(`${BACKEND}/api/tts`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text, language: ttsLanguage }),
          });
          if (token !== ttsTokenRef.current) return resolve();
          if (!resp.ok) {
            setLoadingTs(null);
            setTtsError(
              `Couldn't generate spoken audio for ${languages[ttsLanguage] || ttsLanguage} right now. Please try again.`
            );
            return resolve();
          }
          const blob = await resp.blob();
          if (token !== ttsTokenRef.current) return resolve();
          url = URL.createObjectURL(blob);
        } catch {
          if (token === ttsTokenRef.current) {
            setLoadingTs(null);
            setTtsError("Couldn't reach the text-to-speech service. Please try again.");
          }
          return resolve();
        }
        setLoadingTs(null);

        const audio = new Audio(url);
        audioRef.current = audio;
        setSpeakingTs(ts);
        let settled = false;
        const done = () => {
          if (settled) return;
          settled = true;
          if (url) URL.revokeObjectURL(url);
          if (audioRef.current === audio) audioRef.current = null;
          if (token === ttsTokenRef.current) setSpeakingTs(null);
          resolve();
        };
        audio.onended = done;
        audio.onerror = done;
        audio.onpause = done;
        try {
          await audio.play();
        } catch (err) {
          if (token === ttsTokenRef.current) {
            setSpeakingTs(null);
            // Deliberately do NOT clear audioRef/revoke the URL here: the
            // audio is already fetched and loaded, so a direct follow-up
            // click (retryBlockedAudio) can call play() synchronously and
            // succeed even when this attempt -- which only reached play()
            // after an async fetch -- was refused by the browser's autoplay
            // policy. `settled` stays false, so the eventual
            // onended/onpause/onerror still runs the real cleanup once.
            setBlockedTs(ts);
            setTtsError(
              err instanceof DOMException && err.name === "NotAllowedError"
                ? "Your browser blocked audio playback. Tap the speaker icon again to play it."
                : `Couldn't play audio${err instanceof Error && err.message ? `: ${err.message}` : "."}`
            );
          }
          resolve();
        }
      })();
    });

  // Retries playback for a message already flagged as blocked, WITHOUT
  // re-fetching -- audioRef.current is the same loaded Audio object from
  // playBackendAudio's attempt. Calling .play() here happens synchronously
  // inside the click handler (no await beforehand), which is exactly the
  // "direct result of a user gesture" browsers require.
  const retryBlockedAudio = async (ts: number) => {
    const audio = audioRef.current;
    if (!audio) {
      setBlockedTs(null);
      return;
    }
    try {
      await audio.play();
      setSpeakingTs(ts);
      setBlockedTs(null);
      setTtsError("");
    } catch {
      setTtsError(
        "Still blocked by the browser. Check that this tab/site isn't muted " +
          "(right-click the browser tab, or check the sound icon in the address bar)."
      );
    }
  };

  // Speaks one message, preferring the browser's own built-in speechSynthesis
  // (free, instant, zero backend call) and falling back to cloud TTS
  // (playBackendAudio) only when the browser has no voice for ttsLanguage.
  // Resolves once playback finishes (or fails/is interrupted). `token` is
  // the ttsTokenRef snapshot at call time, so a later stopSpeaking()/newer
  // speakText() call makes every state update here a no-op.
  const speakUtterance = (text: string, ts: number, token: number): Promise<void> => {
    if (!("speechSynthesis" in window)) {
      return playBackendAudio(text, ts, token);
    }

    const langCode = getLanguageCode(ttsLanguage);
    if (hasKnownVoiceGap(langCode)) {
      return playBackendAudio(text, ts, token);
    }

    return new Promise((resolve) => {
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = langCode;
      const voice = findVoiceForLanguage(langCode);
      if (voice) utterance.voice = voice;

      const done = () => {
        if (token === ttsTokenRef.current) setSpeakingTs(null);
        resolve();
      };
      utterance.onstart = () => {
        if (token === ttsTokenRef.current) setSpeakingTs(ts);
      };
      utterance.onend = done;
      utterance.onerror = (event) => {
        if (token !== ttsTokenRef.current) {
          resolve();
          return;
        }
        // A handful of browsers report a missing voice at speak-time
        // instead of (or in addition to) leaving it out of getVoices() --
        // fall back to the cloud instead of just erroring in that case too.
        if (event.error === "language-unavailable" || event.error === "voice-unavailable" || event.error === "synthesis-unavailable" || event.error === "synthesis-failed") {
          resolve(playBackendAudio(text, ts, token));
          return;
        }
        if (event.error !== "canceled" && event.error !== "interrupted") {
          setTtsError("Couldn't play audio using this browser's built-in voice.");
        }
        done();
      };
      window.speechSynthesis.speak(utterance);
    });
  };

  // Speaks exactly one message (text + ts), scoped by speakUtterance's own
  // token check so clicking a different message's speaker icon cleanly
  // supersedes this one.
  const speakText = async (text: string, ts: number) => {
    stopSpeaking();
    const token = ttsTokenRef.current;
    await speakUtterance(text, ts, token);
  };

  // Drains autoQueueRef one message at a time (only one instance ever runs,
  // guarded by autoQueueBusyRef) so simultaneous replies queue up instead of
  // the newest cancelling the still-loading previous one.
  const processAutoQueue = async () => {
    if (autoQueueBusyRef.current) return;
    autoQueueBusyRef.current = true;
    try {
      while (autoQueueRef.current.length > 0) {
        const token = ttsTokenRef.current;
        const next = autoQueueRef.current.shift();
        if (!next) break;
        await speakUtterance(next.text, next.ts, token);
        if (token !== ttsTokenRef.current) {
          // A manual stop/interrupt happened mid-queue -- drop the rest.
          autoQueueRef.current = [];
          break;
        }
      }
    } finally {
      autoQueueBusyRef.current = false;
    }
  };

  const enqueueAutoSpeak = (text: string, ts: number) => {
    autoQueueRef.current.push({ text, ts });
    void processAutoQueue();
  };

  const stopRecording = () => {
    setListening(false);
    try {
      recorderRef.current?.stop();
    } catch {
      // Ignore
    }
    try {
      mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
    } catch {
      // Ignore
    }
    recorderRef.current = null;
    mediaStreamRef.current = null;
  };

  const discardRecording = () => {
    try {
      mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
    } catch {
      // Ignore
    }
    recorderRef.current = null;
    mediaStreamRef.current = null;
    setListening(false);
  };

  const startListening = async () => {
    if (!ASR_SUPPORTED_LANGUAGES.has(ttsLanguage)) {
      const name = languages[ttsLanguage] || ttsLanguage;
      setMicError(
        `Voice input (mic) isn't available for ${name} yet. Type your question instead.`
      );
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      setMicError("Audio recording is not supported in this browser. Please use Chrome or Edge.");
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const preferredMime = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/mp4",
        "audio/ogg;codecs=opus",
      ].find((mime) => window.MediaRecorder.isTypeSupported(mime));
      const recorder = preferredMime
        ? new MediaRecorder(stream, { mimeType: preferredMime })
        : new MediaRecorder(stream);

      const chunks: BlobPart[] = [];
      const startedAt = Date.now();
      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) chunks.push(event.data);
      };
      recorder.onstop = () => {
        try {
          stream.getTracks().forEach((track) => track.stop());
        } catch {
          // Ignore
        }
        recorderRef.current = null;
        mediaStreamRef.current = null;
        const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
        // A recording stopped almost instantly produces a container with a
        // header but no (or too few) actual audio frames -- the backend's
        // decoder then hits an immediate end-of-file. Catch that case here,
        // before wasting a round trip on audio the server can't decode.
        if (Date.now() - startedAt < MIN_RECORDING_MS) {
          setMicError("Recording was too short to hear anything. Hold the mic button and speak for a moment.");
          return;
        }
        if (blob.size) void uploadAudio(blob);
      };

      recorderRef.current = recorder;
      mediaStreamRef.current = stream;
      recorder.start();
      setListening(true);
      setMicError("");
    } catch {
      setListening(false);
      setMicError("Microphone access was denied. Please allow microphone access and try again.");
    }
  };

  const uploadAudio = async (blob: Blob) => {
    setAsrBusy(true);
    try {
      const formData = new FormData();
      formData.append("audio", blob, "recording.webm");
      if (ASR_SUPPORTED_LANGUAGES.has(ttsLanguage)) {
        formData.append("language", ttsLanguage);
      }
      const controller = new AbortController();
      const timeoutMs = 90 * 1000; // First request may load the Whisper model.
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      let resp: Response;
      try {
        resp = await fetch(`${BACKEND}/api/asr`, {
          method: "POST",
          body: formData,
          signal: controller.signal,
        });
      } finally {
        clearTimeout(timer);
      }
      if (!resp.ok) {
        let detail = `Server error ${resp.status}`;
        try {
          const errJson = await resp.json();
          if (errJson?.error) detail = String(errJson.error);
        } catch {
          // Keep the generic message.
        }
        throw new Error(detail);
      }
      const json: { text?: string; language?: string; confidence?: number } = await resp.json();
      const transcript = (json?.text || "").trim();
      if (transcript) {
        setInputText(transcript);
        setMicError("");
      } else {
        setMicError("No speech was produced. Please speak clearly and try again.");
      }
      textareaRef.current?.focus();
    } catch (error) {
      console.error("ASR error:", error);
      setMicError(error instanceof Error ? error.message : "Could not transcribe the audio. Please try again.");
    } finally {
      setAsrBusy(false);
      setListening(false);
    }
  };

  const toggleListening = () => {
    if (asrBusy) return;
    if (listening) {
      stopRecording();
    } else {
      void startListening();
    }
  };

  const handleFileSelected = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;

    if (file.size > MAX_ATTACHMENT_BYTES) {
      setAttachmentError("File too large. Maximum size is 10 MB.");
      return;
    }
    const ext = file.name.includes(".") ? file.name.slice(file.name.lastIndexOf(".")).toLowerCase() : "";
    if (!ALLOWED_ATTACHMENT_EXTENSIONS.has(ext)) {
      setAttachmentError(
        "Unsupported file type. Please upload PDF, Word, Excel, PowerPoint, CSV, or TXT files."
      );
      return;
    }

    setAttachment({ file, name: file.name, size: file.size });
    setAttachmentError("");
  };

  const removeAttachment = () => {
    setAttachment(null);
    setAttachmentError("");
    setUploading(false);
    setUploadProgress(0);
  };

  const uploadAttachment = (session: string): Promise<PendingAttachment> =>
    new Promise((resolve, reject) => {
      const pending = attachment;
      if (!pending) return reject(new Error("No attachment"));
      if (pending.fileId) return resolve(pending);

      const formData = new FormData();
      formData.append("file", pending.file, pending.name);
      formData.append("session_id", session);

      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${BACKEND}/chat/upload`);
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          setUploadProgress(Math.round((event.loaded / event.total) * 100));
        }
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            const json = JSON.parse(xhr.responseText);
            const uploaded: PendingAttachment = {
              ...pending,
              fileId: String(json.file_id ?? ""),
              name: String(json.filename ?? pending.name),
            };
            if (!uploaded.fileId) throw new Error("Missing file_id");
            setAttachment(uploaded);
            resolve(uploaded);
          } catch {
            reject(new Error("File upload failed. Please try again."));
          }
        } else {
          let detail = "File upload failed. Please try again.";
          try {
            const json = JSON.parse(xhr.responseText);
            if (json?.error) detail = String(json.error);
          } catch {
            // Keep the generic message.
          }
          reject(new Error(detail));
        }
      };
      xhr.onerror = () => reject(new Error("File upload failed. Please try again."));
      xhr.send(formData);
    });

  // Shared by both hospital-search triggers (explicit request + the
  // high-risk-file quick-reply button): asks the browser for location, then
  // -- instead of searching off that raw result immediately -- shows a
  // hospital_confirm message (map + draggable pin, see LocationConfirmMap)
  // so the user can visually verify or correct the detected point first.
  // The actual /hospitals/nearby search only runs once the user confirms a
  // position; see runHospitalSearchAt below.
  //
  // RESTORED (this was briefly reverted to a direct geolocation->results
  // flow, then brought back): without this step, a wrong-but-confident
  // browser fix (e.g. WiFi/IP positioning reporting a small `accuracy`
  // while actually ~40km off) produces a full page of real, correctly-
  // sorted hospital results -- just real hospitals near the WRONG point,
  // with only a passive text banner as the tell. The map/pin is the actual
  // correction mechanism; the accuracy banner alone was never sufficient.
  const searchNearbyHospitals = async (urgent: boolean) => {
    try {
      const loc = await getUserLocation();
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          text: "I detected your location below. Please confirm it before I search for hospitals.",
          ts: Date.now(),
          kind: "hospital_confirm",
          pendingLat: loc.lat,
          pendingLng: loc.lng,
          locationAccuracyMeters: loc.accuracy,
          hospitalUrgent: urgent,
        },
      ]);
    } catch {
      // Geolocation denied, unsupported, or timed out -- no coordinate to
      // show a pin at, so fall straight to the GPS-free place-name search
      // (graceful fallback per spec step 3c, never a broken/frozen state).
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          text:
            "I couldn't access your location. You can search for hospitals manually, or enter your city/area below and I'll try that instead.",
          ts: Date.now(),
          kind: "hospital_fallback",
        },
      ]);
    }
  };

  // Runs the actual hospital search against a lat/lng the user has already
  // confirmed (or corrected) on the LocationConfirmMap pin -- never the raw
  // geolocation result directly. Coordinates never leave this function's
  // scope beyond the single fetch call below -- nothing here persists them.
  const runHospitalSearchAt = async (lat: number, lng: number, urgent: boolean) => {
    try {
      const resp = await fetch(`${BACKEND}/hospitals/nearby`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ latitude: lat, longitude: lng }),
      });
      const json: { hospitals?: HospitalResult[]; error?: string } = await resp
        .json()
        .catch(() => ({ error: "Hospital search failed. Please try again." }));

      if (!resp.ok || json.error) {
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            text: json.error || "I couldn't find hospitals near you right now. Please try again.",
            ts: Date.now(),
            kind: "hospital_fallback",
          },
        ]);
        return;
      }

      const hospitals = json.hospitals ?? [];
      if (!hospitals.length) {
        // Even at a user-confirmed point, nothing came back in range --
        // offer the GPS-free alternative (search by city/area name)
        // instead of a dead end.
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            text: "I couldn't find any hospitals near the confirmed location within range.",
            ts: Date.now(),
            kind: "hospital_fallback",
          },
        ]);
        return;
      }

      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          text: `I found ${hospitals.length} hospital${hospitals.length > 1 ? "s" : ""} near you.`,
          ts: Date.now(),
          kind: "hospital_results",
          hospitals,
          hospitalUrgent: urgent,
        },
      ]);
    } catch {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          text: "Hospital search failed due to a network error. Please try again.",
          ts: Date.now(),
          kind: "hospital_fallback",
        },
      ]);
    }
  };

  // GPS-free fallback: search by a typed city/area name via Nominatim's own
  // geocoding (same provider as the coordinate search, see hospital_search.py's
  // find_hospitals_by_place). This is the actual implementation behind the
  // "share your city/area" text above -- previously there was no handler
  // for it at all.
  const searchHospitalsByPlace = async (place: string, urgent: boolean) => {
    const trimmedPlace = place.trim();
    if (!trimmedPlace) return;
    try {
      const resp = await fetch(`${BACKEND}/hospitals/by-place`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ place: trimmedPlace }),
      });
      const json: { hospitals?: HospitalResult[]; error?: string } = await resp
        .json()
        .catch(() => ({ error: "Hospital search failed. Please try again." }));

      if (!resp.ok || json.error) {
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            text: json.error || `I couldn't find hospitals near '${trimmedPlace}'. Please try again.`,
            ts: Date.now(),
            kind: "hospital_fallback",
          },
        ]);
        return;
      }

      const hospitals = json.hospitals ?? [];
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          text: hospitals.length
            ? `I found ${hospitals.length} hospital${hospitals.length > 1 ? "s" : ""} near ${trimmedPlace}.`
            : `I couldn't find any hospitals near '${trimmedPlace}'.`,
          ts: Date.now(),
          kind: hospitals.length ? "hospital_results" : "hospital_fallback",
          hospitals,
          hospitalUrgent: urgent,
        },
      ]);
    } catch {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          text: "Hospital search failed due to a network error. Please try again.",
          ts: Date.now(),
          kind: "hospital_fallback",
        },
      ]);
    }
  };

  const sendMessage = async (rawText: string) => {
    const trimmed = rawText.trim();
    const pendingAttachment = attachment;
    if ((!trimmed && !pendingAttachment) || loading) return;

    // "/help" is a client-side-only command: it never reaches the backend
    // (so it doesn't count against the response limit), and instead of a
    // normal reply it echoes the command, drops a short pointer message in
    // the chat, and always pops up the full interactive guide dialog.
    if (trimmed.toLowerCase() === "/help") {
      setMessages((prev) => [
        ...prev,
        { role: "user", text: trimmed, ts: Date.now() },
        {
          role: "assistant",
          text: "Here's your guide to using the Rural Healthcare AI Assistant 👇",
          ts: Date.now() + 1,
          kind: "normal",
        },
      ]);
      setInputText("");
      resetHeight();
      removeAttachment();
      setShowHelpDialog(true);
      return;
    }

    if (trimmed.length > MAX_INPUT_CHARS) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          text: `Please keep your question within ${MAX_INPUT_CHARS} characters.`,
          ts: Date.now(),
        },
      ]);
      return;
    }

    // Upload the attachment (if any) before posting the chat message, so the
    // backend can look it up by file_id when extracting its text.
    let uploadedFileId: string | undefined;
    let uploadedFileName: string | undefined;
    if (pendingAttachment) {
      setUploading(true);
      setUploadProgress(0);
      try {
        const uploaded = await uploadAttachment(sessionId);
        uploadedFileId = uploaded.fileId;
        uploadedFileName = uploaded.name;
      } catch (err) {
        setUploading(false);
        setAttachmentError(err instanceof Error ? err.message : "File upload failed. Please try again.");
        return;
      }
      setUploading(false);
    }

    setMessages((prev) => [
      ...prev,
      { role: "user", text: trimmed, ts: Date.now(), fileName: uploadedFileName },
    ]);
    setInputText("");
    resetHeight();
    removeAttachment();
    setLoading(true);

    const controller = new AbortController();
    chatAbortControllerRef.current = controller;

    try {
      const resp = await fetch(`${BACKEND}/ai-chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: trimmed,
          session_id: sessionId,
          user_email: effectiveEmail ?? undefined,
          user_id: supabaseUserId ?? undefined,
          language: ttsLanguage,
          file_id: uploadedFileId,
          filename: uploadedFileName,
        }),
        signal: controller.signal,
      });

      if (!resp.ok) throw new Error(`Server error ${resp.status}`);

      const json: {
        reply: string;
        response_count?: number;
        limit_reached?: boolean;
        retry_after_iso?: string | null;
        conversation_cap_reached?: boolean;
        cached?: boolean;
        guardrail_type?: string | null;
        emergency?: boolean;
        session_id?: string;
        model_tier?: "primary" | "fallback" | "portkey" | "kb_only" | "failed";
        hospital_search_requested?: boolean;
        hospital_search_urgent?: boolean;
        high_risk_offer?: { risk_percent: number; message: string };
      } = await resp.json();

      if (json.session_id) setSessionId(json.session_id);

      if (json.response_count !== undefined) setResponseCount(json.response_count);
      setActiveConversationId((prev) => prev ?? sessionId);

      const addAssistant = (
        text: string,
        opts?: { kind?: Message["kind"]; cached?: boolean; modelTier?: Message["modelTier"] }
      ) => {
        const replyTs = Date.now();
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            text,
            ts: replyTs,
            kind: opts?.kind ?? "normal",
            cached: opts?.cached,
            modelTier: opts?.modelTier,
          },
        ]);
        if (ttsEnabled && !json.limit_reached && !opts?.kind) {
          enqueueAutoSpeak(text, replyTs);
        }
      };

      if (json.conversation_cap_reached) {
        // Per-user 10-saved-chat cap: this request never reached the AI at
        // all (see _would_exceed_conversation_cap in app.py) -- just show
        // the block message, no reply/session bookkeeping to do.
        addAssistant(
          json.reply || "You've reached the limit of 10 saved chats per user.",
          { kind: "guardrail" },
        );
      } else if (json.hospital_search_requested) {
        // Trigger 1: explicit "nearest hospital" request. The backend never
        // sees location -- it only answered with this text; the actual
        // search happens client-side via the browser's geolocation prompt.
        addAssistant(json.reply || "Let me find hospitals near you.", { kind: "normal" });
        setResponseCount(json.response_count ?? 0);
        void searchNearbyHospitals(!!json.hospital_search_urgent);
      } else if (json.emergency) {
        addAssistant(json.reply || "", { kind: "emergency" });
        setResponseCount(json.response_count ?? 0);
      } else if (json.guardrail_type) {
        addAssistant(json.reply || "", { kind: "guardrail" });
        setResponseCount(json.response_count ?? 0);
      } else if (json.cached) {
        addAssistant(json.reply || "I'm unable to answer that right now.", { kind: "normal", cached: true });
        setResponseCount(json.response_count ?? 0);
      } else {
        addAssistant(json.reply || "I'm unable to answer that right now.", {
          modelTier: json.model_tier,
        });
        setResponseCount(json.response_count ?? 0);
      }

      // 30-response/5h cooldown: shown as a popup (not just a chat bubble,
      // which addAssistant already added above) so the exact time chat
      // becomes available again is impossible to miss/scroll past.
      if (json.limit_reached && json.retry_after_iso) {
        setCooldownUntilIso(json.retry_after_iso);
      }

      // Trigger 2: high-risk % found in an attached file. Always a SEPARATE
      // follow-up message alongside whichever branch above answered the
      // user's actual question (spec step 5b) -- never folded into it.
      if (json.high_risk_offer) {
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            text: json.high_risk_offer!.message,
            ts: Date.now(),
            kind: "hospital_offer",
            riskPercent: json.high_risk_offer!.risk_percent,
          },
        ]);
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        // User-initiated stop -- not an error, don't show a failure message.
        setMessages((prev) => [
          ...prev,
          { role: "assistant", text: "Message stopped.", ts: Date.now(), kind: "stopped" },
        ]);
      } else {
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            text: "I couldn't connect to the server. Please make sure the backend is running on port 5001 and try again.",
            ts: Date.now(),
          },
        ]);
      }
    } finally {
      // Only clear the loading state / controller ref if this request is
      // still the current one -- if the user stopped this request AND
      // fired a new one before this cleanup ran, the new request's own
      // controller is already in the ref and must not be clobbered here.
      if (chatAbortControllerRef.current === controller) {
        chatAbortControllerRef.current = null;
        setLoading(false);
      }
      refreshConversations();
      textareaRef.current?.focus();
    }
  };

  const stopGenerating = () => {
    chatAbortControllerRef.current?.abort();
    chatAbortControllerRef.current = null;
    setLoading(false);
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value;
    if (value.length <= MAX_INPUT_CHARS) {
      setInputText(value);
      autoResize(e.target);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void sendMessage(inputText);
    }
  };

  const handlePaste = () => {
    window.setTimeout(() => {
      const textarea = textareaRef.current;
      if (!textarea) return;

      if (textarea.value.length > MAX_INPUT_CHARS) {
        textarea.value = textarea.value.slice(0, MAX_INPUT_CHARS);
        setInputText(textarea.value);
      }

      autoResize(textarea);
    }, 0);
  };

  const reset = async () => {
    stopSpeaking();
    try {
      await fetch(`${BACKEND}/clear-session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId }),
      });
    } catch {
      // Ignore failures and still reset the client state.
    } finally {
      setMessages([WELCOME]);
      setInputText("");
      resetHeight();
      setSessionId(makeSessionId());
      setActiveConversationId(null);
      setSessionStartedAt(Date.now());
      setResponseCount(0);
      discardRecording();
      setAsrBusy(false);
      setMicError("");
      setSpeakingTs(null);
      removeAttachment();
      refreshConversations();
      textareaRef.current?.focus();
    }
  };

  // Distinct from reset(): wipes the CURRENT conversation's messages (both
  // here and server-side) but keeps the same session_id and stays selected
  // in the sidebar, instead of starting a brand-new conversation thread.
  // "Start New Chat" (sidebar) already covers the new-conversation case --
  // this was previously a second, redundant "New chat" button in the
  // navbar that did the exact same thing as that one.
  const clearChat = async () => {
    stopSpeaking();
    try {
      await fetch(`${BACKEND}/clear-session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId }),
      });
    } catch {
      // Ignore failures and still clear the visible chat.
    } finally {
      setMessages([WELCOME]);
      setInputText("");
      resetHeight();
      setSessionStartedAt(Date.now());
      setResponseCount(0);
      discardRecording();
      setAsrBusy(false);
      setMicError("");
      setSpeakingTs(null);
      removeAttachment();
      textareaRef.current?.focus();
    }
  };

  const sidebarContent = (
    <>
      <div className="flex items-center justify-between border-b border-emerald-100 px-4 py-3">
        <span className="text-xs font-semibold uppercase tracking-[0.18em] text-emerald-600">Chat history</span>
        {isMobile && (
          <button
            onClick={() => setSidebarOpen(false)}
            className="text-slate-400 transition-colors hover:text-slate-700"
            aria-label="Close chat history"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      <div className="px-3 pt-3">
        <button
          onClick={() => void reset()}
          className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-2xl bg-emerald-600 text-sm font-medium text-white transition-colors hover:bg-emerald-700"
        >
          <Plus className="h-4 w-4" />
          Start New Chat
        </button>
      </div>

      <div className="mt-3 flex-1 overflow-y-auto px-2 pb-4">
        {conversations.length === 0 ? (
          <p className="px-2 py-6 text-center text-xs leading-5 text-slate-400">
            {supabaseUserId
              ? "No saved conversations yet. Start chatting and your conversations will appear here."
              : "Your saved conversations will appear here after you sign in."}
          </p>
        ) : (
          <ul className="space-y-1">
            {conversations.map((conv) => {
              const isActive = activeConversationId === conv.session_id;
              const isRenaming = renamingId === conv.session_id;
              return (
                <li key={conv.session_id} className="group relative">
                  {isRenaming ? (
                    <div className="flex w-full items-center gap-2 rounded-xl border border-emerald-300 bg-white px-3 py-2">
                      <MessageSquare className="h-3.5 w-3.5 shrink-0 text-emerald-700" />
                      <input
                        ref={(el) => {
                          if (el && renameInputRef.current !== el) {
                            renameInputRef.current = el;
                            el.focus();
                            el.select();
                          }
                        }}
                        value={renameDraft}
                        maxLength={50}
                        onChange={(e) => setRenameDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            void saveRename(conv);
                          } else if (e.key === "Escape") {
                            e.preventDefault();
                            cancelRenameRef.current = true;
                            setRenamingId(null);
                          }
                        }}
                        onBlur={() => {
                          if (cancelRenameRef.current) {
                            cancelRenameRef.current = false;
                            return;
                          }
                          void saveRename(conv);
                        }}
                        aria-label={`Rename conversation: ${conv.title || "New chat"}`}
                        className="min-w-0 flex-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-sm text-slate-800 focus:border-emerald-400 focus:outline-none focus:ring-1 focus:ring-emerald-300"
                      />
                    </div>
                  ) : (
                    <button
                      onClick={() => void openConversation(conv)}
                      className={`flex w-full items-start gap-2 rounded-xl px-3 py-2 text-left transition-colors ${
                        isActive ? "bg-emerald-100/80" : "hover:bg-emerald-50"
                      }`}
                    >
                      <MessageSquare
                        className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${
                          isActive ? "text-emerald-700" : "text-slate-400"
                        }`}
                      />
                      <span className="min-w-0">
                        <span
                          className={`block truncate text-sm ${
                            isActive ? "font-semibold text-emerald-900" : "font-medium text-slate-700"
                          }`}
                        >
                          {conv.title || "New chat"}
                        </span>
                        <span className="block text-[11px] text-slate-400">
                          {formatConversationTime(conv.created_at || conv.updated_at)}
                        </span>
                      </span>
                    </button>
                  )}
                  {!isRenaming && (
                    <>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          startRename(conv);
                        }}
                        aria-label={`Rename conversation: ${conv.title || "New chat"}`}
                        title="Rename conversation"
                        className="absolute right-10 top-1/2 inline-flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 opacity-0 transition-opacity hover:bg-slate-50 hover:text-slate-700 group-hover:opacity-100"
                      >
                        <Pencil className="h-3.5 w-3.5" />
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setDeleteTarget(conv);
                        }}
                        aria-label={`Delete conversation: ${conv.title || "New chat"}`}
                        title="Delete conversation"
                        className="absolute right-2 top-1/2 inline-flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-lg border border-rose-200 bg-white text-rose-500 opacity-0 transition-opacity hover:bg-rose-50 group-hover:opacity-100"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </>
  );

  return (
    <Layout>
      <style>{`
        @media print {
          header, footer { display: none !important; }
          body { background: #ffffff !important; }
          * { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
          .no-print { display: none !important; }
          .print-only { display: block !important; }
        }
        .print-only { display: none; }
        .print-transcript { font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }
        .print-transcript h1 { font-size: 22px; margin: 0 0 4px; color: #111827; }
        .print-transcript .print-meta { font-size: 12px; color: #6b7280; margin-bottom: 20px; }
        .print-transcript .print-msg { margin: 0 0 14px; }
        .print-transcript .print-sender { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; margin: 0 0 2px; }
        .print-transcript .print-sender.you { color: #059669; }
        .print-transcript .print-sender.bot { color: #1f2937; }
        .print-transcript .print-time { font-weight: 400; text-transform: none; letter-spacing: 0; color: #9ca3af; }
        .print-transcript .print-text { font-size: 13px; line-height: 1.55; color: #374151; margin: 0; white-space: pre-wrap; }
        .print-transcript .print-footer { margin-top: 24px; padding-top: 12px; border-top: 1px solid #e5e7eb; font-size: 11px; color: #6b7280; }
      `}</style>

      <div className="min-h-[calc(100vh-4rem)] bg-gradient-to-b from-white via-emerald-50/60 to-slate-50 px-4 py-5 md:px-6">
        <div className="mx-auto flex min-h-[calc(100vh-6rem)] w-full max-w-6xl overflow-hidden rounded-[2rem] border border-emerald-100 bg-white/90 shadow-[0_24px_80px_rgba(15,23,42,0.08)] backdrop-blur">
          {!isMobile && sidebarOpen && (
            <aside className="no-print flex w-72 shrink-0 flex-col border-r border-emerald-100 bg-emerald-50/40">
              {sidebarContent}
            </aside>
          )}

          {isMobile && sidebarOpen && (
            <>
              <div
                className="no-print fixed inset-0 z-40 bg-slate-900/30 backdrop-blur-sm"
                onClick={() => setSidebarOpen(false)}
                aria-hidden="true"
              />
              <aside className="no-print fixed inset-y-0 left-0 z-50 flex w-80 max-w-[85vw] flex-col border-r border-emerald-100 bg-white shadow-2xl">
                {sidebarContent}
              </aside>
            </>
          )}

          <div className="flex min-w-0 flex-1 flex-col">
          <div className="border-b border-emerald-100 px-5 py-4 md:px-6">
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div className="flex min-w-0 flex-1 items-center gap-3">
                <button
                  onClick={() => setSidebarOpen((v) => !v)}
                  aria-label={sidebarOpen ? "Hide chat history" : "Show chat history"}
                  title={sidebarOpen ? "Hide chat history" : "Show chat history"}
                  className="no-print inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-emerald-200 bg-emerald-50 text-emerald-700 transition-colors hover:bg-emerald-100"
                >
                  {sidebarOpen ? <PanelLeftClose className="h-4 w-4" /> : <PanelLeftOpen className="h-4 w-4" />}
                </button>
                <div className="min-w-0">
                  <div className="flex items-center gap-2 break-normal text-xs font-semibold uppercase tracking-[0.24em] text-emerald-600">
                    <Sparkles className="h-4 w-4 shrink-0" />
                    Rural Healthcare Assistant
                  </div>
                  <h1 className="mt-2 break-normal text-2xl font-bold text-slate-900 md:text-3xl">Chat with the assistant</h1>
                  <p className="mt-1 max-w-2xl break-normal text-sm text-slate-600">
                    Ask about symptoms, precautions, next steps, or when to seek medical care. You can type or speak.
                  </p>
                </div>
              </div>

              <div className="no-print flex shrink-0 flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() =>
                    setTtsEnabled((v) => {
                      const next = !v;
                      if (!next) stopSpeaking();
                      return next;
                    })
                  }
                  aria-pressed={ttsEnabled}
                  aria-label={ttsEnabled ? "Turn off spoken replies" : "Turn on spoken replies"}
                  title={
                    ttsEnabled
                      ? "Audio is on: new replies will be read aloud automatically"
                      : "Audio is off: replies won't autoplay and per-message speaker icons are hidden"
                  }
                  className={`inline-flex h-10 items-center gap-2 rounded-full border px-4 text-sm font-medium transition-colors ${
                    ttsEnabled
                      ? "border-emerald-600 bg-emerald-600 text-white hover:bg-emerald-700"
                      : "border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100"
                  }`}
                >
                  {ttsEnabled ? <Volume2 className="h-4 w-4" /> : <VolumeX className="h-4 w-4" />}
                  Audio
                </button>
                <button
                  onClick={handleDownloadPdf}
                  className="inline-flex h-10 items-center gap-2 rounded-full border border-emerald-200 bg-emerald-50 px-4 text-sm font-medium text-emerald-700 transition-colors hover:bg-emerald-100"
                >
                  <Download className="h-4 w-4" />
                  Download PDF
                </button>
                <button
                  onClick={() => void clearChat()}
                  title="Clear the messages in this chat (keeps it as the same conversation)"
                  className="inline-flex h-10 items-center gap-2 rounded-full border border-emerald-200 bg-emerald-50 px-4 text-sm font-medium text-emerald-700 transition-colors hover:bg-emerald-100"
                >
                  <Trash2 className="h-4 w-4" />
                  Clear chat
                </button>
              </div>
            </div>

            {ttsError && (
              <div className="no-print mt-3 flex items-center justify-between gap-2 rounded-xl bg-amber-50 px-3 py-2 text-xs font-medium text-amber-700">
                <span className="flex items-center gap-2">
                  <AlertCircle className="h-4 w-4 shrink-0" />
                  {ttsError}
                </span>
                <button
                  type="button"
                  onClick={() => setTtsError("")}
                  aria-label="Dismiss audio error"
                  className="shrink-0 rounded-full px-2 py-0.5 text-amber-500 hover:bg-amber-100 hover:text-amber-700"
                >
                  Dismiss
                </button>
              </div>
            )}

            <div className="mt-4 flex flex-wrap items-center gap-3 text-xs text-slate-500">
              <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-3 py-1">
                <AlertCircle className="h-3.5 w-3.5" />
                Responses used: {responseCount}
              </span>
              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-3 py-1 text-emerald-700">
                <Bot className="h-3.5 w-3.5" />
                Enter sends, Shift+Enter adds a new line
              </span>
              <select
                value={ttsLanguage}
                onChange={(e) => setTtsLanguage(e.target.value)}
                aria-label="Chat language: replies, voice input, and spoken replies"
                title="Chat language: replies, voice input, and spoken replies"
                className="rounded-full border border-emerald-200 bg-white px-2 py-1 text-xs text-slate-600 focus:outline-none"
              >
                {Object.entries(languages).map(([code, name]) => (
                  <option key={code} value={code}>
                    {name}
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={() => setShowHelpDialog(true)}
                aria-label="How to use this chatbot (/help)"
                title="How to use this chatbot -- type /help anytime"
                className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-white px-3 py-1 text-emerald-700 transition-colors hover:bg-emerald-50"
              >
                <Info className="h-3.5 w-3.5" />
                /help
              </button>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto px-4 py-5 md:px-6">
            <div className="space-y-4">
              {messages.map((message, index) => {
                const isUser = message.role === "user";
                const bubbleClass =
                  message.kind === "guardrail"
                    ? "rounded-bl-md border border-amber-300 bg-amber-50 text-amber-900"
                    : message.kind === "emergency"
                    ? "rounded-bl-md border border-purple-300 bg-purple-600 text-white"
                    : message.kind === "hospital_offer"
                    ? "rounded-bl-md border-2 border-red-300 bg-red-50 text-red-900"
                    : message.kind === "error"
                    ? "rounded-bl-md border border-red-200 bg-red-50 text-red-800"
                    : message.kind === "stopped"
                    ? "rounded-bl-md border border-slate-200 border-dashed bg-slate-50 italic text-slate-500"
                    : "rounded-bl-md border border-slate-200 bg-white text-slate-800";
                return (
                  <div key={`${message.ts}-${index}`} className={`flex gap-3 ${isUser ? "justify-end" : "justify-start"}`}>
                    {!isUser && (
                      <div
                        className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full shadow-sm ${
                          message.kind === "emergency"
                            ? "bg-purple-500 text-white"
                            : message.kind === "hospital_offer"
                            ? "bg-red-500 text-white"
                            : "bg-emerald-500 text-white"
                        }`}
                      >
                        {message.kind === "guardrail" ? (
                          <ShieldAlert className="h-5 w-5" />
                        ) : message.kind === "emergency" ? (
                          <Heart className="h-5 w-5" />
                        ) : message.kind === "hospital_offer" ? (
                          <AlertTriangle className="h-5 w-5" />
                        ) : (
                          <Bot className="h-5 w-5" />
                        )}
                      </div>
                    )}

                    <div
                      className={`max-w-[85%] rounded-3xl px-4 py-3 text-sm leading-6 shadow-sm md:max-w-[70%] ${
                        isUser
                          ? "rounded-br-md bg-emerald-600 text-white"
                          : bubbleClass
                      }`}
                    >
                      {isUser && message.fileName && (
                        <div className="mb-1.5">
                          <FileChip name={message.fileName} />
                        </div>
                      )}
                      {(message.kind === "guardrail" ||
                        message.kind === "error") && (
                        <p className="mb-1 flex items-center gap-1.5 text-xs font-bold uppercase tracking-wide opacity-80">
                          {message.kind === "guardrail" ? "Safety notice" : "Error"}
                        </p>
                      )}
                      {message.kind === "emergency" && (
                        <p className="mb-1 flex items-center gap-1.5 text-xs font-bold uppercase tracking-wide text-purple-100">
                          <Heart className="h-3.5 w-3.5" /> Support available
                        </p>
                      )}
                      {formatMessage(message.text).map((line, lineIndex) => (
                        <p key={lineIndex} className={lineIndex === 0 && (message.kind === "guardrail" || message.kind === "emergency" || message.kind === "error") ? "mt-0" : lineIndex === 0 ? "" : "mt-2"}>
                          {line}
                        </p>
                      ))}

                      {/* Trigger 2: high-risk-file quick reply -- asked once
                          per spec 5d, so the button disappears the moment
                          it's clicked rather than staying clickable again. */}
                      {message.kind === "hospital_offer" && !message.offerHandled && (
                        <button
                          type="button"
                          onClick={() => {
                            const clickedTs = message.ts;
                            setMessages((prev) =>
                              prev.map((m) => (m.ts === clickedTs ? { ...m, offerHandled: true } : m)),
                            );
                            void searchNearbyHospitals(false);
                          }}
                          className="mt-3 inline-flex items-center gap-1.5 rounded-full bg-red-600 px-4 py-2 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-red-700"
                        >
                          <MapPin className="h-3.5 w-3.5" /> Yes, find hospitals
                        </button>
                      )}

                      {/* Map-preview confirmation step -- shown BEFORE any
                          hospital search runs. See LocationConfirmMap for
                          why: the browser's raw geolocation result (and its
                          self-reported accuracy) is never trusted blindly. */}
                      {message.kind === "hospital_confirm" && !message.confirmed && (
                        <LocationConfirmMap
                          initialLat={message.pendingLat!}
                          initialLng={message.pendingLng!}
                          accuracyMeters={message.locationAccuracyMeters ?? 0}
                          onConfirm={(lat, lng) => {
                            const clickedTs = message.ts;
                            setMessages((prev) =>
                              prev.map((m) => (m.ts === clickedTs ? { ...m, confirmed: true } : m)),
                            );
                            void runHospitalSearchAt(lat, lng, !!message.hospitalUrgent);
                          }}
                        />
                      )}
                      {message.kind === "hospital_confirm" && message.confirmed && (
                        <p className="mt-2 flex items-center gap-1.5 text-xs font-medium text-emerald-700">
                          <MapPin className="h-3.5 w-3.5" /> Location confirmed — searching for
                          hospitals…
                        </p>
                      )}

                      {/* Both triggers render through this once results are in. */}
                      {message.kind === "hospital_results" && (
                        <div className="mt-2 space-y-2">
                          {message.hospitalUrgent && (
                            <div className="flex items-start gap-2 rounded-xl border border-red-300 bg-red-50 p-2.5 text-xs font-medium text-red-800">
                              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                              <span>
                                If this is a medical emergency, please call emergency services
                                immediately while you travel to the nearest hospital.
                              </span>
                            </div>
                          )}
                          {/* No post-search accuracy banner here -- by the
                              time hospital_results exists, the user already
                              confirmed/corrected the point on the
                              LocationConfirmMap pin (see hospital_confirm
                              above), so re-warning about the raw browser
                              accuracy number would just be stale/confusing. */}
                          {(message.hospitals ?? []).map((hospital, hIndex) => (
                            <HospitalCard key={`${hospital.name}-${hIndex}`} hospital={hospital} />
                          ))}
                        </div>
                      )}

                      {message.kind === "hospital_fallback" && (
                        <div className="mt-2 space-y-2">
                          <form
                            onSubmit={(e) => {
                              e.preventDefault();
                              const place = placeQueries[message.ts] ?? "";
                              void searchHospitalsByPlace(place, !!message.hospitalUrgent);
                              setPlaceQueries((prev) => ({ ...prev, [message.ts]: "" }));
                            }}
                            className="flex items-center gap-1.5"
                          >
                            <input
                              type="text"
                              value={placeQueries[message.ts] ?? ""}
                              onChange={(e) =>
                                setPlaceQueries((prev) => ({ ...prev, [message.ts]: e.target.value }))
                              }
                              placeholder="Enter your city or area"
                              aria-label="City or area for hospital search"
                              className="min-w-0 flex-1 rounded-full border border-emerald-200 bg-white px-3 py-1.5 text-xs text-slate-700 outline-none focus:border-emerald-400"
                            />
                            <button
                              type="submit"
                              disabled={!(placeQueries[message.ts] ?? "").trim()}
                              className="inline-flex shrink-0 items-center gap-1 rounded-full bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              <MapPin className="h-3.5 w-3.5" /> Search
                            </button>
                          </form>
                          <a
                            href={GENERIC_HOSPITALS_MAPS_URL}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1.5 text-xs font-semibold text-emerald-700 underline underline-offset-2 hover:text-emerald-800"
                          >
                            <MapPin className="h-3.5 w-3.5" /> Search hospitals near me on Google Maps
                          </a>
                        </div>
                      )}

                      {message.cached && (
                        <span className="mt-1.5 inline-flex items-center gap-1 text-[10px] font-medium text-slate-400">
                          <Zap className="h-3 w-3" /> Instant
                        </span>
                      )}
                      {message.modelTier === "fallback" && (
                        <span className="mt-1.5 block text-[10px] font-medium text-slate-400">
                          Answered using backup AI model
                        </span>
                      )}
                      {message.modelTier === "kb_only" && (
                        <span className="mt-1.5 block text-[10px] font-medium text-slate-400">
                          Answered from knowledge base (AI engine unavailable)
                        </span>
                      )}
                    </div>

                    {!isUser && ttsEnabled && (
                      <div className="flex flex-col items-center gap-1 self-start pt-1">
                        <button
                          type="button"
                          onClick={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            if (speakingTs === message.ts || loadingTs === message.ts) {
                              stopSpeaking();
                            } else if (blockedTs === message.ts) {
                              // Retry with the already-loaded audio, synchronously
                              // from this click -- see retryBlockedAudio's comment.
                              void retryBlockedAudio(message.ts);
                            } else {
                              void speakText(message.text, message.ts);
                            }
                          }}
                          aria-label={
                            speakingTs === message.ts
                              ? "Stop speaking"
                              : loadingTs === message.ts
                              ? "Loading audio"
                              : blockedTs === message.ts
                              ? "Playback blocked -- tap to retry"
                              : "Speak reply"
                          }
                          title={
                            speakingTs === message.ts
                              ? "Stop speaking"
                              : loadingTs === message.ts
                              ? "Loading audio..."
                              : blockedTs === message.ts
                              ? "Browser blocked playback -- tap to retry"
                              : "Speak reply"
                          }
                          className={`inline-flex h-8 w-8 items-center justify-center rounded-full border transition-colors ${
                            speakingTs === message.ts
                              ? "border-emerald-500 bg-emerald-500 text-white"
                              : blockedTs === message.ts
                              ? "border-amber-400 bg-amber-50 text-amber-600 hover:bg-amber-100"
                              : "border-slate-200 bg-white text-slate-500 hover:bg-emerald-50 hover:text-emerald-600"
                          }`}
                        >
                          {speakingTs === message.ts ? (
                            <Square className="h-3.5 w-3.5" />
                          ) : loadingTs === message.ts ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : blockedTs === message.ts ? (
                            <AlertCircle className="h-3.5 w-3.5" />
                          ) : (
                            <Volume2 className="h-3.5 w-3.5" />
                          )}
                        </button>
                      </div>
                    )}

                    {isUser && (
                      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-slate-900 text-white shadow-sm">
                        <User className="h-5 w-5" />
                      </div>
                    )}
                  </div>
                );
              })}

              {loading && (
                <div className="flex gap-3">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-emerald-500 text-white shadow-sm">
                    <Bot className="h-5 w-5" />
                  </div>
                  <div className="flex items-center gap-2 rounded-3xl rounded-bl-md border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500 shadow-sm">
                    <Loader2 className="h-4 w-4 animate-spin text-emerald-600" />
                    Thinking...
                  </div>
                  <button
                    type="button"
                    onClick={stopGenerating}
                    className="inline-flex shrink-0 items-center gap-1.5 self-center rounded-full border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 shadow-sm transition-colors hover:border-red-300 hover:bg-red-50 hover:text-red-600"
                  >
                    <Square className="h-3 w-3 fill-current" />
                    Stop
                  </button>
                </div>
              )}

              <div ref={bottomRef} />
            </div>
          </div>

          {messages.length === 1 && !loading && (
            <div className="flex flex-wrap gap-2 border-t border-slate-100 px-4 py-4 md:px-6">
              {SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  onClick={() => void sendMessage(suggestion)}
                  className="rounded-full border border-emerald-200 bg-emerald-50 px-4 py-2 text-xs font-medium text-emerald-700 transition-colors hover:bg-emerald-100"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          )}

          <div className="border-t border-slate-200 bg-white px-4 py-3 md:px-6">
            {listening && (
              <div className="mb-2 flex items-center gap-2 rounded-xl bg-red-50 px-3 py-2 text-xs font-medium text-red-600">
                <MicOff className="h-4 w-4 animate-pulse" />
                Listening... speak your question, then click the stop button when done.
              </div>
            )}
            {asrBusy && (
              <div className="mb-2 flex items-center gap-2 rounded-xl bg-emerald-50 px-3 py-2 text-xs font-medium text-emerald-700">
                <Loader2 className="h-4 w-4 animate-spin" />
                Transcribing your recording...
              </div>
            )}
            {micError && (
              <div className="mb-2 flex items-center gap-2 rounded-xl bg-amber-50 px-3 py-2 text-xs font-medium text-amber-700">
                <AlertCircle className="h-4 w-4" />
                {micError}
              </div>
            )}

            {attachmentError && (
              <div className="mb-2 flex items-center gap-2 rounded-xl bg-amber-50 px-3 py-2 text-xs font-medium text-amber-700">
                <AlertCircle className="h-4 w-4" />
                {attachmentError}
              </div>
            )}

            {attachment && (
              <div className="mb-2 flex items-center gap-2">
                <FileChip name={attachment.name} size={attachment.size} onRemove={uploading ? undefined : removeAttachment} />
                {uploading && (
                  <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-600">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    Uploading{uploadProgress > 0 ? ` ${uploadProgress}%` : "..."}
                  </span>
                )}
              </div>
            )}

            <input
              ref={fileInputRef}
              type="file"
              accept={ATTACHMENT_ACCEPT}
              onChange={handleFileSelected}
              className="hidden"
            />

            <div className="flex items-end gap-3">
              <div className="relative flex-1">
                <textarea
                  ref={textareaRef}
                  value={inputText}
                  onChange={handleInputChange}
                  onKeyDown={handleKeyDown}
                  onPaste={handlePaste}
                  placeholder={listening ? "Listening..." : "Ask a health question..."}
                  rows={1}
                  disabled={loading}
                  className="chat-input w-full"
                  style={{
                    paddingBottom: "20px",
                    paddingRight: "70px",
                  }}
                />

                <div className="pointer-events-none absolute bottom-2 right-3 text-[10px] leading-none text-slate-400">
                  {inputText.length}/{MAX_INPUT_CHARS}
                </div>
              </div>

              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={loading || uploading || !!attachment}
                aria-label="Attach a file"
                title="Attach a file (PDF, Word, Excel, PowerPoint, CSV, or TXT — max 10 MB)"
                className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border border-slate-200 bg-white text-slate-600 transition-colors hover:bg-emerald-50 hover:text-emerald-600 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <Paperclip className="h-4 w-4" />
              </button>

              <button
                onClick={toggleListening}
                disabled={loading || asrBusy || (!listening && !ASR_SUPPORTED_LANGUAGES.has(ttsLanguage))}
                aria-label={
                  listening
                    ? "Stop voice input"
                    : ASR_SUPPORTED_LANGUAGES.has(ttsLanguage)
                    ? "Start voice input"
                    : `Voice input isn't available for ${languages[ttsLanguage] || ttsLanguage}`
                }
                title={
                  listening
                    ? "Stop voice input"
                    : ASR_SUPPORTED_LANGUAGES.has(ttsLanguage)
                    ? "Start voice input"
                    : `Voice input isn't available for ${languages[ttsLanguage] || ttsLanguage}`
                }
                className={`inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
                  listening
                    ? "animate-pulse border-red-300 bg-red-50 text-red-600"
                    : "border-slate-200 bg-white text-slate-600 hover:bg-emerald-50 hover:text-emerald-600"
                }`}
              >
                {listening ? <MicOff className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
              </button>

              <button
                onClick={loading ? stopGenerating : () => void sendMessage(inputText)}
                disabled={loading ? false : !inputText.trim() && !attachment}
                aria-label={loading ? "Stop generating" : "Send message"}
                title={loading ? "Stop generating" : "Send message"}
                className={`inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl text-white transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
                  loading ? "bg-red-500 hover:bg-red-600" : "bg-emerald-600 hover:bg-emerald-700"
                }`}
              >
                {loading ? <Square className="h-4 w-4 fill-current" /> : <Send className="h-4 w-4" />}
              </button>
            </div>

            <p className="mt-2 text-center text-[11px] text-slate-400">
              Rural Healthcare AI can make mistakes. Please double-check important information.
            </p>
          </div>
        </div>
        </div>

        <div ref={printRef} className="print-only">
          <div className="print-transcript">
            <h1>Rural Healthcare AI — Chat Transcript</h1>
            <p className="print-meta">
              Session started: {new Date(sessionStartedAt).toLocaleString()}
            </p>
            {messages
              .filter((m) => m !== WELCOME && m.text !== WELCOME.text)
              .map((m, i) => (
                <div key={`${m.ts}-${i}`} className="print-msg">
                  <p className={`print-sender ${m.role === "user" ? "you" : "bot"}`}>
                    {m.role === "user" ? "You" : "Rural Healthcare AI"}
                    <span className="print-time"> — {new Date(m.ts).toLocaleString()}</span>
                  </p>
                  <p className="print-text">{m.text}</p>
                </div>
              ))}
            <p className="print-footer">
              AI Assistant can make mistakes. Please verify important information with a healthcare professional.
            </p>
          </div>
        </div>

        <ConfirmDeleteModal
          open={deleteTarget != null}
          title="Delete conversation?"
          message="Delete this conversation? This cannot be undone."
          onConfirm={() => deleteTarget && void handleDeleteConversation(deleteTarget)}
          onCancel={() => setDeleteTarget(null)}
        />

        {/* 30-response/5h cooldown popup -- pops up the moment the backend
            reports the limit was hit (see cooldownUntilIso in sendMessage),
            showing the EXACT time chat becomes available again rather than
            just "come back later". */}
        <AlertDialog open={cooldownUntilIso != null} onOpenChange={(open) => !open && setCooldownUntilIso(null)}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>You've reached your message limit</AlertDialogTitle>
              <AlertDialogDescription>
                You've used all {30} messages available for now. Please come back after{" "}
                <span className="font-semibold text-slate-800">
                  {cooldownUntilIso
                    ? new Date(cooldownUntilIso).toLocaleString(undefined, {
                        weekday: "short",
                        hour: "numeric",
                        minute: "2-digit",
                      })
                    : ""}
                </span>
                , when your cooling-off period ends and you can chat again.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogAction onClick={() => setCooldownUntilIso(null)}>Got it</AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>

        {/* "/help" guide popup -- pops up every time the user types /help
            (also reachable via the /help button next to the language
            selector), giving a full blueprint of what the assistant can
            do so users don't have to guess. Built as a plain overlay
            (like the delete-conversation dialog above) rather than the
            Radix AlertDialog so it can hold real block content -- headings,
            lists -- instead of the single <p> the AlertDialogDescription
            primitive allows. */}
        {showHelpDialog && (
          <div className="no-print fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4 py-8 backdrop-blur-sm">
            <div className="flex max-h-[85vh] w-full max-w-lg flex-col rounded-3xl bg-white shadow-2xl">
              <div className="flex items-start justify-between gap-4 border-b border-slate-100 px-6 py-5">
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-emerald-500 text-white">
                    <Bot className="h-5 w-5" />
                  </div>
                  <div>
                    <h2 className="text-lg font-semibold text-slate-900">
                      How to use Rural Healthcare AI
                    </h2>
                    <p className="text-xs text-slate-500">Your quick guide to this chatbot</p>
                  </div>
                </div>
                <button
                  onClick={() => setShowHelpDialog(false)}
                  className="shrink-0 rounded-full p-1 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900"
                  aria-label="Close help guide"
                >
                  <X className="h-5 w-5" />
                </button>
              </div>

              <div className="flex-1 space-y-5 overflow-y-auto px-6 py-5 text-sm leading-6 text-slate-600">
                <section>
                  <h3 className="mb-1.5 flex items-center gap-1.5 text-sm font-semibold text-slate-900">
                    <Sparkles className="h-4 w-4 text-emerald-600" /> What I can help with
                  </h3>
                  <ul className="list-disc space-y-1 pl-5">
                    <li>Answering general healthcare questions in plain language</li>
                    <li>Explaining symptoms, precautions, and when they may need attention</li>
                    <li>Reviewing an uploaded report/file and estimating disease risk from it</li>
                    <li>Guiding you on when a symptom needs urgent/emergency care</li>
                    <li>Finding hospitals near you when you ask, or after a high-risk result</li>
                  </ul>
                </section>

                <section>
                  <h3 className="mb-1.5 flex items-center gap-1.5 text-sm font-semibold text-slate-900">
                    <MessageSquare className="h-4 w-4 text-emerald-600" /> Questions I can handle
                  </h3>
                  <p>
                    Symptom checks ("I have fever and cough, what could it be?"), precautions and
                    home care, general disease/medication information, and "should I go to a
                    hospital?" style questions. I'm an assistant, not a doctor -- for diagnosis,
                    prescriptions, or emergencies, please consult a healthcare professional or
                    local emergency services.
                  </p>
                </section>

                <section>
                  <h3 className="mb-1.5 flex items-center gap-1.5 text-sm font-semibold text-slate-900">
                    <Paperclip className="h-4 w-4 text-emerald-600" /> Attachments I can read
                  </h3>
                  <p className="mb-1.5">
                    One file per message, up to 10 MB. I'll read the text/data inside it to answer
                    your question or estimate risk:
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {[".pdf", ".doc/.docx", ".xlsx", ".pptx", ".csv", ".txt"].map(
                      (ext) => (
                        <span
                          key={ext}
                          className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600"
                        >
                          {ext}
                        </span>
                      ),
                    )}
                  </div>
                </section>

                <section>
                  <h3 className="mb-1.5 flex items-center gap-1.5 text-sm font-semibold text-slate-900">
                    <Mic className="h-4 w-4 text-emerald-600" /> Voice &amp; language
                  </h3>
                  <ul className="list-disc space-y-1 pl-5">
                    <li>Type your question, or use the mic button to speak it -- works in English, Hindi, Kannada, Tamil, and Telugu</li>
                    <li>Pick a reply language from the dropdown to get answers in that language</li>
                    <li>Tap the speaker icon on any reply to hear it read aloud using your browser's voice</li>
                  </ul>
                </section>

                <section>
                  <h3 className="mb-1.5 flex items-center gap-1.5 text-sm font-semibold text-slate-900">
                    <AlertCircle className="h-4 w-4 text-emerald-600" /> Commands &amp; limits
                  </h3>
                  <ul className="list-disc space-y-1 pl-5">
                    <li>
                      <code className="rounded bg-slate-100 px-1 py-0.5 text-xs">/help</code> --
                      shows this guide any time
                    </li>
                    <li>Up to {MAX_INPUT_CHARS} characters per message</li>
                    <li>Up to 30 responses every 5 hours, and 10 saved conversations per account</li>
                  </ul>
                </section>

                <div className="flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 p-3 text-xs font-medium text-red-800">
                  <Heart className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>
                    In a medical emergency, call your local emergency services immediately --
                    don't wait for a chatbot reply.
                  </span>
                </div>
              </div>

              <div className="border-t border-slate-100 px-6 py-4">
                <button
                  onClick={() => setShowHelpDialog(false)}
                  className="inline-flex h-11 w-full items-center justify-center rounded-2xl bg-emerald-600 px-4 text-sm font-semibold text-white transition-colors hover:bg-emerald-700"
                >
                  Got it
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </Layout>
  );
}
