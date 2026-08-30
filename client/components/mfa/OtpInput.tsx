/**
 * Six-box one-time-password input used by every MFA screen (challenge page,
 * enrolment step, unenrol confirmation, recovery flow).
 *
 * Why this component exists: Google/GitHub-style 2FA entry needs a split
 * input with auto-advancing focus, backspace navigation, paste support, and
 * a shake animation on invalid codes — behaviours that are awkward to repeat
 * across three different screens, so they live here once. Each box is a
 * large tap target and uses inputMode="numeric" so mobile keyboards open in
 * digit mode.
 */

import { useEffect, useRef, useState } from "react";

interface OtpInputProps {
  length?: number;
  disabled?: boolean;
  autoFocus?: boolean;
  /** Increment to shake the boxes and clear every digit (e.g. after a failed attempt). */
  shakeKey?: number;
  onChange?: (code: string) => void;
  /** Called automatically once every box is filled. */
  onComplete?: (code: string) => void;
  className?: string;
}

const SHAKE_FRAMES = 8;

export default function OtpInput({
  length = 6,
  disabled = false,
  autoFocus = true,
  shakeKey = 0,
  onChange,
  onComplete,
  className = "",
}: OtpInputProps) {
  const [digits, setDigits] = useState<string[]>(() => Array.from({ length }, () => ""));
  const inputsRef = useRef<(HTMLInputElement | null)[]>([]);

  useEffect(() => {
    setDigits(Array.from({ length }, () => ""));
  }, [length, shakeKey]);

  useEffect(() => {
    if (autoFocus && !disabled) inputsRef.current[0]?.focus();
  }, [autoFocus, disabled, shakeKey]);

  const emit = (next: string[]) => {
    const code = next.join("");
    onChange?.(code);
    if (next.every((d) => d !== "")) onComplete?.(code);
  };

  const handleChange = (index: number, raw: string) => {
    const onlyDigit = raw.replace(/\D/g, "").slice(-1);
    const next = [...digits];
    next[index] = onlyDigit;
    setDigits(next);
    emit(next);
    if (onlyDigit && index < length - 1) {
      inputsRef.current[index + 1]?.focus();
    }
  };

  const handleKeyDown = (index: number, e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Backspace") {
      if (digits[index] !== "") {
        const next = [...digits];
        next[index] = "";
        setDigits(next);
      } else if (index > 0) {
        const next = [...digits];
        next[index - 1] = "";
        setDigits(next);
        inputsRef.current[index - 1]?.focus();
      }
      return;
    }
    if (e.key === "ArrowLeft" && index > 0) {
      inputsRef.current[index - 1]?.focus();
    }
    if (e.key === "ArrowRight" && index < length - 1) {
      inputsRef.current[index + 1]?.focus();
    }
  };

  const handlePaste = (e: React.ClipboardEvent<HTMLInputElement>) => {
    const pasted = e.clipboardData.getData("text") || "";
    const onlyDigits = pasted.replace(/\D/g, "").slice(0, length);
    if (!onlyDigits) return;

    e.preventDefault();
    const next = Array.from({ length }, (_, i) => onlyDigits[i] ?? "");
    setDigits(next);
    emit(next);
    const focusIndex = Math.min(onlyDigits.length, length - 1);
    inputsRef.current[focusIndex]?.focus();
  };

  return (
    <div className={`flex justify-center gap-2 sm:gap-2.5 ${className}`} key={shakeKey} data-otp-shake={shakeKey > 0 ? "1" : undefined}>
      <style>{`@keyframes mfa-shake { 0%,100%{transform:translateX(0)} ${[20,40,60,80].map((p, i) => `${p}%{transform:translateX(${i % 2 === 0 ? "-6px" : "6px"})}`).join(" ")} }`}</style>
      <style>{`[data-otp-shake="1"] .otp-box { animation: mfa-shake 0.4s ease-in-out; }`}</style>
      {Array.from({ length }, (_, index) => (
        <input
          key={index}
          ref={(el) => {
            inputsRef.current[index] = el;
          }}
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          pattern="[0-9]*"
          maxLength={1}
          disabled={disabled}
          value={digits[index]}
          onChange={(e) => handleChange(index, e.target.value)}
          onKeyDown={(e) => handleKeyDown(index, e)}
          onPaste={handlePaste}
          aria-label={`Digit ${index + 1}`}
          className="otp-box w-11 h-12 sm:w-12 sm:h-12 text-center text-xl font-semibold border border-border rounded-lg text-foreground bg-white focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary transition disabled:opacity-50"
        />
      ))}
    </div>
  );
}