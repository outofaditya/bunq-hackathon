import { ExternalLink } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export type SourceLink = {
  /** Domain shown in the chip, e.g. "booking.com" */
  site: string;
  /** Single emoji shown as the icon */
  icon: string;
  /** Headline shown on the card */
  title: string;
  /** One-line subtitle (price, rating, plan, etc.) */
  subtitle: string;
  /** Real, valid URL — clicking opens it in a new tab */
  url: string;
  /** Mood color for the left border + glow */
  tone?: "sky" | "coral" | "amber" | "violet" | "mint";
};

const TONE_BORDER: Record<NonNullable<SourceLink["tone"]>, string> = {
  sky:    "border-l-status-scheduled",
  coral:  "border-l-accent-coral",
  amber:  "border-l-status-upcoming",
  violet: "border-l-status-project",
  mint:   "border-l-status-complete",
};
const TONE_GLOW: Record<NonNullable<SourceLink["tone"]>, string> = {
  sky:    "hover:shadow-[0_18px_44px_-10px_rgba(105,179,255,0.35)] hover:border-status-scheduled/40",
  coral:  "hover:shadow-[0_18px_44px_-10px_rgba(255,111,143,0.4)]  hover:border-accent-coral/40",
  amber:  "hover:shadow-[0_18px_44px_-10px_rgba(255,180,83,0.35)]  hover:border-status-upcoming/40",
  violet: "hover:shadow-[0_18px_44px_-10px_rgba(183,136,255,0.4)]  hover:border-status-project/40",
  mint:   "hover:shadow-[0_18px_44px_-10px_rgba(91,203,143,0.35)]  hover:border-status-complete/40",
};

// Mission-aware sources. URLs are real, the listings are illustrative.
export const SOURCES_BY_MISSION: Record<string, { heading: string; links: SourceLink[] }> = {
  travel: {
    heading: "Booking sources",
    links: [
      { site: "booking.com",  icon: "🛏",  title: "Park Hyatt Tokyo",            subtitle: "5★ · Shinjuku · €420 / night",  url: "https://www.booking.com/searchresults.html?ss=Tokyo",                                                                       tone: "sky" },
      { site: "booking.com",  icon: "🌆", title: "Hotel Gracery Shinjuku",      subtitle: "4★ · Godzilla view · €180 / night", url: "https://www.booking.com/hotel/jp/gracery-shinjuku.html",                                                                tone: "sky" },
      { site: "hotels.com",   icon: "🏨",  title: "Aman Tokyo",                  subtitle: "5★ · Otemachi · €890 / night",  url: "https://www.hotels.com/ho157655/aman-tokyo-tokyo-japan/",                                                                  tone: "violet" },
      { site: "expedia.com",  icon: "✈️",  title: "Conrad Tokyo",                subtitle: "5★ · Shiodome · €350 / night",  url: "https://www.expedia.com/Tokyo-Hotels.d6053839.Travel-Guide-Hotels",                                                        tone: "coral" },
      { site: "agoda.com",    icon: "🌏",  title: "Cerulean Tower Tokyu",        subtitle: "4★ · Shibuya · €240 / night",   url: "https://www.agoda.com/cerulean-tower-tokyu-hotel/hotel/tokyo-jp.html",                                                     tone: "mint" },
      { site: "tripadvisor",  icon: "🗺",  title: "Travellers' Choice 2024",    subtitle: "Top picks for Tokyo",           url: "https://www.tripadvisor.com/Tourism-g14134-Tokyo_Tokyo_Prefecture_Kanto-Vacations.html",                                  tone: "amber" },
    ],
  },
  weekend: {
    heading: "Restaurant sources",
    links: [
      { site: "thefork.nl",    icon: "🍴",  title: "Da Portare Via",         subtitle: "Italian · €€ · Jordaan",        url: "https://www.thefork.nl/restaurants/amsterdam-c40741",                                                                  tone: "coral" },
      { site: "thefork.nl",    icon: "🥖",  title: "Café-Restaurant Amsterdam", subtitle: "Dutch · €€ · Westerpark",   url: "https://www.thefork.nl/restaurant/cafe-restaurant-amsterdam-r9081",                                                    tone: "coral" },
      { site: "opentable.com", icon: "🥢",  title: "Yamazato",               subtitle: "Japanese · €€€€ · 1★ Michelin", url: "https://www.opentable.com/r/yamazato-amsterdam",                                                                       tone: "violet" },
      { site: "michelin.com",  icon: "⭐",   title: "De Kas",                  subtitle: "Garden-to-table · €€€",         url: "https://guide.michelin.com/en/noord-holland/amsterdam/restaurant/de-kas",                                              tone: "amber" },
      { site: "iens.nl",       icon: "🍷",  title: "Restaurant Vinkeles",    subtitle: "French · €€€€ · 1★",            url: "https://www.iens.nl/restaurant/22091/amsterdam-vinkeles",                                                              tone: "mint" },
      { site: "ticketmaster",  icon: "🎟",  title: "Concerts this Friday",   subtitle: "Live in Amsterdam",            url: "https://www.ticketmaster.nl/browse/concerts-rid-10001",                                                                tone: "sky" },
    ],
  },
  payday: {
    heading: "Bills & subscriptions",
    links: [
      { site: "duwo.nl",       icon: "🏠",  title: "DUWO tenant portal",     subtitle: "Rent autopay setup",            url: "https://www.duwo.nl/en/tenants/my-duwo/",                                                                              tone: "sky" },
      { site: "spotify.com",   icon: "🎵",  title: "Spotify Premium Family", subtitle: "€17.99 / mo · 6 accounts",      url: "https://www.spotify.com/nl/premium/",                                                                                  tone: "mint" },
      { site: "netflix.com",   icon: "🎬",  title: "Netflix Standard",       subtitle: "€13.99 / mo · 1080p",           url: "https://www.netflix.com/signup/planform",                                                                              tone: "coral" },
      { site: "trainmore.nl",  icon: "💪",  title: "TrainMore membership",   subtitle: "€60 / mo · all locations",      url: "https://www.trainmore.nl/abonnement",                                                                                  tone: "amber" },
      { site: "tellow.nl",     icon: "📊",  title: "Tellow bookkeeping",     subtitle: "€10 / mo · self-employed",      url: "https://www.tellow.nl/",                                                                                               tone: "violet" },
      { site: "vodafoneziggo", icon: "📱",  title: "Vodafone Red 20 GB",     subtitle: "€32.50 / mo · sim only",        url: "https://www.vodafone.nl/shop/sim-only",                                                                                tone: "sky" },
    ],
  },
  tax: {
    heading: "Tax-invoice references",
    links: [
      { site: "belastingdienst", icon: "🧾",  title: "Aangifte / OZB lookup",  subtitle: "Government tax portal",       url: "https://www.belastingdienst.nl/wps/wcm/connect/nl/home",                                                              tone: "coral" },
      { site: "amsterdam.nl",    icon: "🏛",  title: "Gemeente Amsterdam",     subtitle: "Local tax & permits",         url: "https://www.amsterdam.nl/en/civil-affairs/",                                                                            tone: "sky" },
      { site: "iban.com",        icon: "🔢", title: "IBAN format check",      subtitle: "Verify the recipient code",   url: "https://www.iban.com/iban-checker",                                                                                    tone: "mint" },
      { site: "swift.com",       icon: "🌐",  title: "BIC / SWIFT lookup",     subtitle: "Verify the bank",             url: "https://www.swift.com/standards/data-standards/iban-international-bank-account-number",                                tone: "violet" },
    ],
  },
};

export function SourceSidebar({
  mission,
  highlightCount = 0,
}: {
  mission: keyof typeof SOURCES_BY_MISSION | string | null;
  /** How many cards should glow as "checked" so far. Drives the staggered
   *  reveal that simulates a live scrape. */
  highlightCount?: number;
}) {
  const set = mission && SOURCES_BY_MISSION[mission as keyof typeof SOURCES_BY_MISSION];
  if (!set) return null;
  const { heading, links } = set;

  return (
    <Card className="h-full flex flex-col p-0 overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/70 shrink-0">
        <div className="label-uc text-muted-foreground">{heading}</div>
        <div className="flex items-center gap-2">
          <span className="relative w-1.5 h-1.5">
            <span className="absolute inset-0 rounded-full bg-status-complete animate-ping opacity-75" />
            <span className="absolute inset-0 rounded-full bg-status-complete" />
          </span>
          <span className="label-uc tabular text-status-complete">live</span>
        </div>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-2">
        <AnimatePresence initial={false}>
          {links.map((link, i) => {
            const tone = link.tone || "sky";
            const checked = i < highlightCount;
            return (
              <motion.a
                key={link.url}
                href={link.url}
                target="_blank"
                rel="noopener noreferrer"
                initial={{ opacity: 0, x: 14 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.04 + i * 0.06, duration: 0.34, ease: [0.16, 1, 0.3, 1] }}
                whileHover={{ y: -3, scale: 1.02 }}
                whileTap={{ scale: 0.98 }}
                className={cn(
                  "group relative block rounded-md border border-border/80 bg-card/80 backdrop-blur-sm",
                  "border-l-2 px-3 py-2.5 cursor-pointer overflow-hidden",
                  "transition-[border-color,box-shadow] duration-200 ease-[cubic-bezier(0.2,0,0,1)]",
                  TONE_BORDER[tone],
                  TONE_GLOW[tone],
                )}
              >
                {/* Sliding shimmer that runs across the card on hover */}
                <span
                  className="pointer-events-none absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity"
                  style={{
                    background: "linear-gradient(115deg, transparent 0%, rgba(255,255,255,0.06) 50%, transparent 100%)",
                    backgroundSize: "200% 100%",
                    animation: "shimmer 2.4s linear infinite",
                  }}
                />

                <div className="relative flex items-start gap-3">
                  <div className="text-2xl leading-none shrink-0 transition-transform duration-200 group-hover:scale-110 group-hover:rotate-[-4deg]">
                    {link.icon}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-body text-foreground truncate font-medium">
                        {link.title}
                      </span>
                      {checked && (
                        <motion.span
                          initial={{ scale: 0 }}
                          animate={{ scale: 1 }}
                          className={cn(
                            "shrink-0 inline-block w-1.5 h-1.5 rounded-full",
                            tone === "sky"    && "bg-status-scheduled",
                            tone === "coral"  && "bg-accent-coral",
                            tone === "amber"  && "bg-status-upcoming",
                            tone === "violet" && "bg-status-project",
                            tone === "mint"   && "bg-status-complete",
                          )}
                        />
                      )}
                    </div>
                    <div className="text-meta text-muted-foreground tabular truncate mt-0.5">
                      {link.subtitle}
                    </div>
                    <div className="flex items-center gap-1.5 mt-1.5 opacity-70 group-hover:opacity-100 transition-opacity">
                      <span className="label-uc text-paper-400 group-hover:text-foreground transition-colors">
                        {link.site}
                      </span>
                      <ExternalLink className="w-3 h-3 text-paper-400 group-hover:text-accent-coral group-hover:translate-x-0.5 transition-transform" />
                    </div>
                  </div>
                </div>
              </motion.a>
            );
          })}
        </AnimatePresence>
      </div>
    </Card>
  );
}
