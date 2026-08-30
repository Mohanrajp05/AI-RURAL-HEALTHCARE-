import { Layout } from "@/components/Layout";
import { useNavigate } from "react-router-dom";
import { Mail, MessageSquare, Phone } from "lucide-react";

// Gmail compose link is the exact same target already used in the footer
// and Privacy Policy's "contact us" mention -- kept identical here so every
// entry point opens the same pre-filled compose window.
const GMAIL_COMPOSE_URL =
  "https://mail.google.com/mail/?view=cm&fs=1&to=ruralhealthcareai@gmail.com&su=Feedback%20%E2%80%93%20Rural%20Healthcare%20System";

export default function ContactUs() {
  const navigate = useNavigate();

  // Opens the shared feedback modal that lives in Layout.tsx (rendered once
  // for every route) -- see the `?feedback=open` effect there. Using the
  // query param instead of local state means this page never has to
  // duplicate the modal, its form, or the /send-feedback wiring.
  const openFeedback = () => navigate("/contact-us?feedback=open");

  return (
    <Layout>
      <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
        <h1 className="text-3xl font-bold text-foreground mb-2">Contact Us</h1>
        <p className="text-sm text-muted-foreground mb-8">
          Have a question, an issue, or something to share? Reach us any of the ways below.
        </p>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="bg-white border border-border rounded-xl p-5 shadow-sm">
            <div className="flex items-center gap-2 text-muted-foreground text-sm mb-2">
              <Phone className="w-4 h-4 text-primary" /> Phone
            </div>
            <p className="text-foreground font-medium">+91 1234567896</p>
          </div>

          <div className="bg-white border border-border rounded-xl p-5 shadow-sm">
            <div className="flex items-center gap-2 text-muted-foreground text-sm mb-2">
              <Mail className="w-4 h-4 text-primary" /> Email
            </div>
            <a
              href={GMAIL_COMPOSE_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary font-medium hover:underline break-all"
            >
              ruralhealthcareai@gmail.com
            </a>
          </div>

          <div className="bg-white border border-border rounded-xl p-5 shadow-sm">
            <div className="flex items-center gap-2 text-muted-foreground text-sm mb-2">
              <MessageSquare className="w-4 h-4 text-primary" /> Feedback
            </div>
            <p className="text-sm text-muted-foreground mb-3">
              Prefer an in-app form? Send us feedback (with an optional star rating) directly.
            </p>
            <button
              onClick={openFeedback}
              className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors"
            >
              Send Feedback
            </button>
          </div>
        </div>
      </div>
    </Layout>
  );
}
