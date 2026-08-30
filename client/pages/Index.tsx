import { useState, useEffect } from "react";
import { Layout } from "@/components/Layout";
import { Link } from "react-router-dom";
import { ArrowRight, Users, Zap, Shield, TrendingUp, Activity, Clock, ChevronLeft, ChevronRight, MessageCircle } from "lucide-react";

const slides = [
  { src: "/med img 3.jpg", alt: "Medical Image 3" },
  { src: "/med img 2.jpg", alt: "Medical Image 2" },
  { src: "/med img 1.jpg", alt: "Medical Image 1" },
  { src: "/Rural img.jpg", alt: "Rural Healthcare" },
];

export default function Index() {
  const [current, setCurrent] = useState(0);

  useEffect(() => {
    const timer = setInterval(() => {
      setCurrent((prev) => (prev + 1) % slides.length);
    }, 3500);
    return () => clearInterval(timer);
  }, []);

  const prev = () => setCurrent((c) => (c - 1 + slides.length) % slides.length);
  const next = () => setCurrent((c) => (c + 1) % slides.length);

  return (
    <Layout>
      {/* Hero Section */}
      <section className="bg-gradient-to-br from-primary/5 via-accent/5 to-secondary/5 py-16 md:py-24">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="grid md:grid-cols-2 gap-12 items-center">
            <div className="space-y-6">
              <div className="inline-block">
                <span className="px-4 py-2 bg-primary/10 text-primary font-semibold text-sm rounded-full">
                  AI-Powered Healthcare
                </span>
              </div>
              
              <h1 className="text-4xl md:text-5xl font-bold text-foreground leading-tight">
                Healthcare Services for <span className="text-primary">Rural Communities</span>
              </h1>
              
              <p className="text-lg text-muted-foreground max-w-md">
                An intelligent diagnostic system that helps health workers provide accurate, immediate medical assessments using AI technology. Simple to use, accessible to all.
              </p>
              
              <div className="flex flex-col sm:flex-row gap-4 pt-4">
                <Link 
                  to="/assess"
                  className="inline-flex items-center justify-center gap-2 px-6 py-3 bg-primary text-primary-foreground font-semibold rounded-lg hover:bg-primary/90 transition-colors shadow-lg hover:shadow-xl"
                >
                  Start Assessment
                  <ArrowRight className="w-4 h-4" />
                </Link>
                <Link 
                  to="/ai-assistant"
                  className="inline-flex items-center justify-center gap-2 px-6 py-3 border-2 border-primary text-primary font-semibold rounded-lg hover:bg-primary/5 transition-colors"
                >
                  <MessageCircle className="w-4 h-4" />
                  AI Assistant
                </Link>
                <a 
                  href="#features"
                  className="inline-flex items-center justify-center px-6 py-3 border border-border text-foreground font-semibold rounded-lg hover:bg-gray-50 transition-colors"
                >
                  Learn More
                </a>
              </div>
            </div>

            <div className="relative hidden md:block order-first md:order-last">
              <div
                className="absolute inset-0 bg-cover bg-center opacity-50"
                style={{ backgroundImage: "url('/Future-of-Healthcare.jpg'), url('/Rural.jpg')", backgroundBlendMode: "overlay" }}
              ></div>
              <img
                src="/Future-of-Healthcare.jpg"
                alt="Future of Healthcare"
                className="relative rounded-2xl opacity-80 w-full h-auto object-cover"
              />
            </div>
          </div>
        </div>
      </section>

      {/* Features Section */}
      <section id="features" className="py-16 md:py-24 bg-white">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-12">
            <h2 className="text-3xl md:text-4xl font-bold text-foreground mb-4">
              Why Choose Our System
            </h2>
            <p className="text-lg text-muted-foreground max-w-2xl mx-auto">
              Built specifically for rural health workers with simplicity and accuracy in mind.
            </p>
          </div>

          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-8">
            {/* Feature 1 */}
            <div className="p-6 border border-border rounded-xl hover:shadow-lg transition-all hover:border-primary/30">
              <div className="w-12 h-12 bg-primary/10 rounded-lg flex items-center justify-center mb-4">
                <Zap className="w-6 h-6 text-primary" />
              </div>
              <h3 className="text-xl font-semibold text-foreground mb-2">Instant Diagnosis</h3>
              <p className="text-muted-foreground">
                Get AI-powered disease predictions in seconds. No waiting, no complexity.
              </p>
            </div>

            {/* Feature 2 */}
            <div className="p-6 border border-border rounded-xl hover:shadow-lg transition-all hover:border-primary/30">
              <div className="w-12 h-12 bg-primary/10 rounded-lg flex items-center justify-center mb-4">
                <Activity className="w-6 h-6 text-primary" />
              </div>
              <h3 className="text-xl font-semibold text-foreground mb-2">Easy Data Entry</h3>
              <p className="text-muted-foreground">
                Simple form to input age, blood pressure, sugar levels, and symptoms.
              </p>
            </div>

            {/* Feature 3 */}
            <div className="p-6 border border-border rounded-xl hover:shadow-lg transition-all hover:border-primary/30">
              <div className="w-12 h-12 bg-primary/10 rounded-lg flex items-center justify-center mb-4">
                <Shield className="w-6 h-6 text-primary" />
              </div>
              <h3 className="text-xl font-semibold text-foreground mb-2">Accurate Results</h3>
              <p className="text-muted-foreground">
                AI-powered predictions with risk assessment percentages for better decision making.
              </p>
            </div>

            {/* Feature 4 */}
            <div className="p-6 border border-border rounded-xl hover:shadow-lg transition-all hover:border-primary/30">
              <div className="w-12 h-12 bg-primary/10 rounded-lg flex items-center justify-center mb-4">
                <Users className="w-6 h-6 text-primary" />
              </div>
              <h3 className="text-xl font-semibold text-foreground mb-2">Built for Health Workers</h3>
              <p className="text-muted-foreground">
                Designed with rural health workers in mind. User-friendly and accessible.
              </p>
            </div>

            {/* Feature 5 */}
            <div className="p-6 border border-border rounded-xl hover:shadow-lg transition-all hover:border-primary/30">
              <div className="w-12 h-12 bg-primary/10 rounded-lg flex items-center justify-center mb-4">
                <Clock className="w-6 h-6 text-primary" />
              </div>
              <h3 className="text-xl font-semibold text-foreground mb-2">24/7 Available</h3>
              <p className="text-muted-foreground">
                Round-the-clock access to healthcare assessment capabilities anytime.
              </p>
            </div>

            {/* Feature 6 */}
            <div className="p-6 border border-border rounded-xl hover:shadow-lg transition-all hover:border-primary/30">
              <div className="w-12 h-12 bg-primary/10 rounded-lg flex items-center justify-center mb-4">
                <TrendingUp className="w-6 h-6 text-primary" />
              </div>
              <h3 className="text-xl font-semibold text-foreground mb-2">Track Progress</h3>
              <p className="text-muted-foreground">
                Monitor patient health trends and maintain comprehensive health records.
              </p>
            </div>

            {/* Feature 7 — AI Assistant */}
            <Link
              to="/ai-assistant"
              className="p-6 border-2 border-primary/30 bg-primary/5 rounded-xl hover:shadow-lg transition-all hover:border-primary/60 hover:bg-primary/10 group col-span-1 md:col-span-2 lg:col-span-1"
            >
              <div className="w-12 h-12 bg-primary/20 rounded-lg flex items-center justify-center mb-4">
                <MessageCircle className="w-6 h-6 text-primary" />
              </div>
              <h3 className="text-xl font-semibold text-foreground mb-2 group-hover:text-primary transition-colors">AI Health Assistant</h3>
              <p className="text-muted-foreground">
                Ask any health question and get instant, clear guidance on symptoms, first aid, nutrition, and more.
              </p>
              <span className="inline-flex items-center gap-1 mt-3 text-sm font-medium text-primary">
                Chat now <ArrowRight className="w-3.5 h-3.5" />
              </span>
            </Link>
          </div>
        </div>
      </section>

      {/* How It Works Section */}
      <section className="py-16 md:py-24 bg-gray-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-12">
            <h2 className="text-3xl md:text-4xl font-bold text-foreground mb-4">
              How It Works
            </h2>
            <p className="text-lg text-muted-foreground max-w-2xl mx-auto">
              Three simple steps to get health assessments
            </p>
          </div>

          <div className="grid md:grid-cols-3 gap-8 relative">
            {/* Step 1 */}
            <div className="text-center">
              <div className="inline-flex items-center justify-center w-16 h-16 bg-primary text-white rounded-full font-bold text-xl mb-4">
                1
              </div>
              <h3 className="text-xl font-semibold text-foreground mb-2">Enter Patient Data</h3>
              <p className="text-muted-foreground mb-4">
                Input patient information including age, blood pressure, sugar levels, and symptoms
              </p>
            </div>

            {/* Step 2 */}
            <div className="text-center">
              <div className="inline-flex items-center justify-center w-16 h-16 bg-primary text-white rounded-full font-bold text-xl mb-4">
                2
              </div>
              <h3 className="text-xl font-semibold text-foreground mb-2">AI Analysis</h3>
              <p className="text-muted-foreground mb-4">
                Our AI system analyzes the data and processes the symptoms in seconds
              </p>
            </div>

            {/* Step 3 */}
            <div className="text-center">
              <div className="inline-flex items-center justify-center w-16 h-16 bg-primary text-white rounded-full font-bold text-xl mb-4">
                3
              </div>
              <h3 className="text-xl font-semibold text-foreground mb-2">Get Results</h3>
              <p className="text-muted-foreground mb-4">
                Receive disease predictions with risk percentages to support your diagnosis
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* CTA Section with Image Carousel */}
      <section className="py-16 md:py-32 bg-gradient-to-r from-primary to-accent">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
          <h2 className="text-3xl md:text-4xl font-bold text-white mb-4">
            Ready to Help Your Patients?
          </h2>
          <p className="text-lg text-white/90 max-w-2xl mx-auto mb-12">
            Start using the Rural Healthcare System today. Provide better care to your community with AI-powered insights.
          </p>

          {/* Carousel */}
          <div className="relative max-w-4xl mx-auto">
            {/* Slides */}
            <div className="overflow-hidden rounded-2xl shadow-2xl">
              <div
                className="flex transition-transform duration-700 ease-in-out"
                style={{ transform: `translateX(-${current * 100}%)` }}
              >
                {slides.map((slide, i) => (
                  <div key={i} className="min-w-full">
                    <img
                      src={slide.src}
                      alt={slide.alt}
                      className="w-full h-72 md:h-[420px] object-cover"
                    />
                  </div>
                ))}
              </div>
            </div>

            {/* Prev / Next buttons */}
            <button
              onClick={prev}
              aria-label="Previous image"
              className="absolute left-3 top-1/2 -translate-y-1/2 bg-black/40 hover:bg-black/60 text-white rounded-full p-2 transition-colors"
            >
              <ChevronLeft className="w-6 h-6" />
            </button>
            <button
              onClick={next}
              aria-label="Next image"
              className="absolute right-3 top-1/2 -translate-y-1/2 bg-black/40 hover:bg-black/60 text-white rounded-full p-2 transition-colors"
            >
              <ChevronRight className="w-6 h-6" />
            </button>

            {/* Dots */}
            <div className="flex justify-center gap-2 mt-5">
              {slides.map((_, i) => (
                <button
                  key={i}
                  onClick={() => setCurrent(i)}
                  aria-label={`Go to slide ${i + 1}`}
                  className={`w-3 h-3 rounded-full transition-all ${
                    i === current ? "bg-white scale-125" : "bg-white/40 hover:bg-white/70"
                  }`}
                />
              ))}
            </div>
          </div>
        </div>
      </section> 
    </Layout>
  );
}
