import { Layout } from "@/components/Layout";

export default function TermsOfService() {
  return (
    <Layout>
      <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
        <h1 className="text-3xl font-bold text-foreground mb-8">Terms of Service</h1>

        <div className="space-y-6 text-sm text-muted-foreground leading-relaxed">
          <section>
            <h2 className="text-lg font-semibold text-foreground mb-2">1. Acceptance of Terms</h2>
            <p>
              By accessing and using the Rural Healthcare System platform, you agree to be bound by
              these Terms of Service. If you do not agree with any part of these terms, please do not
              use our services.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-foreground mb-2">2. Description of Service</h2>
            <p>
              Rural Healthcare System provides an AI-powered health risk assessment tool designed to
              assist healthcare workers in rural areas. The platform analyzes patient data such as 
         lab results, and symptoms to generate preliminary health risk assessments.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-foreground mb-2">3. Medical Disclaimer</h2>
            <p>
              The assessments provided by this platform are for informational purposes only and do not
              constitute medical advice, diagnosis, or treatment. Always seek the advice of a qualified
              healthcare provider for any medical condition.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-foreground mb-2">4. User Responsibilities</h2>
            <ul className="list-disc pl-5 space-y-1">
              <li>Provide accurate and complete patient information.</li>
              <li>Use the platform only for its intended healthcare assessment purposes.</li>
              <li>Maintain the confidentiality of your account credentials.</li>
              <li>Comply with all applicable laws and regulations.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-foreground mb-2">5. Intellectual Property</h2>
            <p>
              All content, features, and functionality of this platform — including the machine learning
              models, text, graphics, and software — are owned by Rural Healthcare System and are
              protected by intellectual property laws.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-foreground mb-2">6. Limitation of Liability</h2>
            <p>
              Rural Healthcare System shall not be liable for any indirect, incidental, or consequential
              damages arising from the use of this platform. The system is a decision-support tool and
              does not replace professional medical judgment.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-foreground mb-2">7. Modifications</h2>
            <p>
              We reserve the right to modify these Terms of Service at any time. Continued use of the
              platform after changes constitutes acceptance of the updated terms.
            </p>
          </section>

          <p className="text-xs text-muted-foreground pt-4">Last updated: March 2026</p>
        </div>
      </div>
    </Layout>
  );
}
