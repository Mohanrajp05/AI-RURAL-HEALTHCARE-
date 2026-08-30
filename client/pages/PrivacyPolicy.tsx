import { Layout } from "@/components/Layout";

export default function PrivacyPolicy() {
  return (
    <Layout>
      <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
        <h1 className="text-3xl font-bold text-foreground mb-8">Privacy Policy</h1>

        <div className="space-y-6 text-sm text-muted-foreground leading-relaxed">
          <section>
            <h2 className="text-lg font-semibold text-foreground mb-2">1. Introduction</h2>
            <p>
              Rural Healthcare System ("we", "our", "us") is committed to protecting your privacy.
              This Privacy Policy explains how we collect, use, and safeguard your personal information
              when you use our AI-powered healthcare assessment platform.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-foreground mb-2">2. Information We Collect</h2>
            <ul className="list-disc pl-5 space-y-1">
              <li>Personal details such as name and age provided during health assessments.</li>
              <li>Health data including blood pressure, heart rate, temperature, sugar level, lab results, and symptoms.</li>
              <li>Account information such as email address and login credentials.</li>
              <li>Usage data including pages visited and interaction patterns.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-foreground mb-2">3. How We Use Your Information</h2>
            <ul className="list-disc pl-5 space-y-1">
              <li>To provide AI-powered health risk assessments.</li>
              <li>To improve the accuracy of our machine learning models.</li>
              <li>To communicate important health-related updates.</li>
              <li>To maintain and improve our platform.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-foreground mb-2">4. Data Security</h2>
            <p>
              We implement industry-standard security measures to protect your data, including encryption
              in transit and at rest. Access to personal health information is restricted to authorized
              healthcare personnel only.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-foreground mb-2">5. Data Sharing</h2>
            <p>
              We do not sell or share your personal health data with third parties for marketing purposes.
              Data may be shared with healthcare providers involved in your care, or when required by law.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-foreground mb-2">6. Your Rights</h2>
            <p>
              You have the right to access, correct, or delete your personal data. To exercise these rights,
              contact us at <a href="https://mail.google.com/mail/?view=cm&fs=1&to=ruralhealthcareai@gmail.com" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">ruralhealthcareai@gmail.com</a>.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-foreground mb-2">7. Changes to This Policy</h2>
            <p>
              We may update this Privacy Policy from time to time. Changes will be posted on this page
              with the updated effective date.
            </p>
          </section>

          <p className="text-xs text-muted-foreground pt-4">Last updated: March 2026</p>
        </div>
      </div>
    </Layout>
  );
}
