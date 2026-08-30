import { Loader2, Trash2, X } from "lucide-react";

// Shared in-app confirmation modal for destructive delete actions -- replaces
// the browser's native window.confirm() popup everywhere a delete needs
// confirmation (chat history, patient records, delete-all, etc.) so the UI
// never breaks out to an unstyled native dialog. Styling matches the
// teal/emerald app theme: rounded-3xl white card, red/rose destructive
// confirm button, neutral cancel button.
interface ConfirmDeleteModalProps {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  // Shows a spinner on the confirm button and disables both buttons while
  // the delete request is in flight, so a slow/duplicate click can't fire
  // the delete twice.
  loading?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDeleteModal({
  open,
  title,
  message,
  confirmLabel = "Delete",
  cancelLabel = "Cancel",
  loading = false,
  onConfirm,
  onCancel,
}: ConfirmDeleteModalProps) {
  if (!open) return null;

  return (
    <div
      className="no-print fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4 backdrop-blur-sm"
      // Clicking the backdrop only cancels -- it can never trigger the
      // delete itself, matching a click on the Cancel button.
      onClick={() => !loading && onCancel()}
    >
      <div
        className="w-full max-w-sm rounded-3xl bg-white p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-4">
          <h2 className="text-xl font-semibold text-slate-900">{title}</h2>
          <button
            onClick={onCancel}
            disabled={loading}
            className="text-slate-500 transition-colors hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-50"
            aria-label="Close delete confirmation"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <p className="mt-3 text-sm leading-6 text-slate-600">{message}</p>
        <div className="mt-6 flex items-center gap-3">
          <button
            onClick={onCancel}
            disabled={loading}
            className="inline-flex h-11 flex-1 items-center justify-center rounded-2xl border border-slate-200 bg-white px-4 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {cancelLabel}
          </button>
          <button
            onClick={onConfirm}
            disabled={loading}
            className="inline-flex h-11 flex-1 items-center justify-center rounded-2xl bg-rose-600 px-4 text-sm font-medium text-white transition-colors hover:bg-rose-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loading ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Trash2 className="mr-2 h-4 w-4" />
            )}
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
