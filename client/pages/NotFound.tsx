import { Layout } from "@/components/Layout";
import { useLocation } from "react-router-dom";
import { useEffect } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, ArrowRight } from "lucide-react";

const NotFound = () => {
  const location = useLocation();

  useEffect(() => {
    console.error(
      "404 Error: User attempted to access non-existent route:",
      location.pathname,
    );
  }, [location.pathname]);

  return (
    <Layout>
      <div className="min-h-[70vh] flex items-center justify-center px-4">
        <div className="text-center space-y-6 max-w-md">
          <div className="flex justify-center">
            <div className="w-20 h-20 bg-primary/10 rounded-full flex items-center justify-center">
              <AlertCircle className="w-10 h-10 text-primary" />
            </div>
          </div>

          <div>
            <h1 className="text-6xl font-bold text-primary mb-2">404</h1>
            <p className="text-2xl font-semibold text-foreground mb-2">Page Not Found</p>
            <p className="text-muted-foreground">
              The page you're looking for doesn't exist. It might have been moved or the URL might be incorrect.
            </p>
          </div>

          <div className="flex flex-col sm:flex-row gap-3 justify-center pt-4">
            <Link
              to="/"
              className="inline-flex items-center justify-center gap-2 px-6 py-3 bg-primary text-primary-foreground font-semibold rounded-lg hover:bg-primary/90 transition-colors"
            >
              Return Home
              <ArrowRight className="w-4 h-4" />
            </Link>
            <Link
              to="/assess"
              className="inline-flex items-center justify-center px-6 py-3 border-2 border-primary text-primary font-semibold rounded-lg hover:bg-primary/5 transition-colors"
            >
              Health Assessment
            </Link>
          </div>
        </div>
      </div>
    </Layout>
  );
};

export default NotFound;
