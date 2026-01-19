(function () {
  "use strict";

  // ============================================
  // GRAPHQL HELPERS
  // ============================================

  async function graphqlQuery(query, variables = {}) {
    const response = await fetch("/graphql", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ query, variables }),
    });
    const result = await response.json();
    if (result.errors) {
      console.error("[PerformerRating] GraphQL error:", result.errors);
      throw new Error(result.errors[0].message);
    }
    return result.data;
  }

  /**
   * Update performer rating via GraphQL
   * @param {string} performerId - The performer's ID
   * @param {number} rating100 - Rating value (1-100)
   * @returns {Promise<Object>} Updated performer data
   */
  async function updatePerformerRating(performerId, rating100) {
    const mutation = `
      mutation UpdatePerformerRating($id: ID!, $rating: Int!) {
        performerUpdate(input: {
          id: $id,
          rating100: $rating
        }) {
          id
          rating100
        }
      }
    `;

    const result = await graphqlQuery(mutation, {
      id: performerId,
      rating: Math.max(0, Math.min(100, Math.round(rating100)))
    });

    console.log(`[PerformerRating] Updated performer ${performerId} rating to ${rating100}`);
    return result.performerUpdate;
  }

  /**
   * Get performer rating by ID
   * @param {string} performerId - The performer's ID
   * @returns {Promise<number|null>} Rating value or null if not rated
   */
  async function getPerformerRating(performerId) {
    const query = `
      query GetPerformerRating($id: ID!) {
        findPerformer(id: $id) {
          id
          rating100
        }
      }
    `;

    const result = await graphqlQuery(query, { id: performerId });
    return result.findPerformer ? result.findPerformer.rating100 : null;
  }

  // ============================================
  // RATING UI COMPONENTS
  // ============================================

  /**
   * Create a star rating widget
   * @param {number|null} currentRating - Current rating100 value (0-100) or null
   * @param {string} performerId - Performer ID for updates
   * @returns {HTMLElement} Star rating container element
   */
  function createStarRating(currentRating, performerId) {
    const container = document.createElement("div");
    container.className = "pr-star-rating";
    container.dataset.performerId = performerId;

    // Convert rating100 to 5-star scale (0-100 -> 0-5)
    const starValue = currentRating !== null ? currentRating / 20 : 0;
    const fullStars = Math.floor(starValue);
    const hasHalfStar = starValue - fullStars >= 0.5;

    // Create 5 stars
    for (let i = 1; i <= 5; i++) {
      const star = document.createElement("span");
      star.className = "pr-star";
      star.dataset.value = i;

      if (i <= fullStars) {
        star.classList.add("pr-star-filled");
        star.innerHTML = "★";
      } else if (i === fullStars + 1 && hasHalfStar) {
        star.classList.add("pr-star-half");
        star.innerHTML = "★";
      } else {
        star.classList.add("pr-star-empty");
        star.innerHTML = "☆";
      }

      // Click handler for rating
      star.addEventListener("click", async (e) => {
        e.preventDefault();
        e.stopPropagation();
        
        const newStarValue = parseInt(star.dataset.value);
        const newRating100 = newStarValue * 20; // Convert back to rating100
        
        try {
          await updatePerformerRating(performerId, newRating100);
          updateStarDisplay(container, newRating100);
          showRatingFeedback(container, newRating100);
        } catch (err) {
          console.error("[PerformerRating] Failed to update rating:", err);
          showRatingError(container);
        }
      });

      // Hover effects
      star.addEventListener("mouseenter", () => {
        previewStarHover(container, parseInt(star.dataset.value));
      });

      container.appendChild(star);
    }

    // Reset hover preview when mouse leaves
    container.addEventListener("mouseleave", () => {
      updateStarDisplay(container, currentRating);
    });

    // Add rating text
    const ratingText = document.createElement("span");
    ratingText.className = "pr-rating-text";
    ratingText.textContent = currentRating !== null ? `${currentRating}` : "--";
    container.appendChild(ratingText);

    // Prevent card click when interacting with rating
    container.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
    });

    return container;
  }

  /**
   * Update star display based on rating
   * @param {HTMLElement} container - Star rating container
   * @param {number|null} rating100 - Rating value (0-100) or null
   */
  function updateStarDisplay(container, rating100) {
    const stars = container.querySelectorAll(".pr-star");
    const starValue = rating100 !== null ? rating100 / 20 : 0;
    const fullStars = Math.floor(starValue);
    const hasHalfStar = starValue - fullStars >= 0.5;

    stars.forEach((star, index) => {
      const value = index + 1;
      star.classList.remove("pr-star-filled", "pr-star-half", "pr-star-empty", "pr-star-preview");

      if (value <= fullStars) {
        star.classList.add("pr-star-filled");
        star.innerHTML = "★";
      } else if (value === fullStars + 1 && hasHalfStar) {
        star.classList.add("pr-star-half");
        star.innerHTML = "★";
      } else {
        star.classList.add("pr-star-empty");
        star.innerHTML = "☆";
      }
    });

    // Update rating text
    const ratingText = container.querySelector(".pr-rating-text");
    if (ratingText) {
      ratingText.textContent = rating100 !== null ? `${rating100}` : "--";
    }
  }

  /**
   * Preview star hover state
   * @param {HTMLElement} container - Star rating container
   * @param {number} hoverValue - Star value being hovered (1-5)
   */
  function previewStarHover(container, hoverValue) {
    const stars = container.querySelectorAll(".pr-star");
    stars.forEach((star, index) => {
      const value = index + 1;
      star.classList.remove("pr-star-preview");
      if (value <= hoverValue) {
        star.classList.add("pr-star-preview");
        star.innerHTML = "★";
      }
    });
  }

  /**
   * Show brief feedback after rating update
   * @param {HTMLElement} container - Star rating container
   * @param {number} rating100 - New rating value
   */
  function showRatingFeedback(container, rating100) {
    const ratingText = container.querySelector(".pr-rating-text");
    if (ratingText) {
      ratingText.classList.add("pr-feedback-success");
      setTimeout(() => {
        ratingText.classList.remove("pr-feedback-success");
      }, 500);
    }
  }

  /**
   * Show error feedback after failed rating update
   * @param {HTMLElement} container - Star rating container
   */
  function showRatingError(container) {
    const ratingText = container.querySelector(".pr-rating-text");
    if (ratingText) {
      ratingText.classList.add("pr-feedback-error");
      setTimeout(() => {
        ratingText.classList.remove("pr-feedback-error");
      }, 1000);
    }
  }

  // ============================================
  // PERFORMER CARD DETECTION & INJECTION
  // ============================================

  /**
   * Extract performer ID from a performer card element
   * @param {HTMLElement} card - Performer card element
   * @returns {string|null} Performer ID or null
   */
  function getPerformerIdFromCard(card) {
    // Try getting from card's link href (e.g., /performers/123)
    const link = card.querySelector("a[href*='/performers/']");
    if (link) {
      const href = link.getAttribute("href");
      const match = href.match(/\/performers\/(\d+)/);
      if (match) {
        return match[1];
      }
    }

    // Try data attributes
    if (card.dataset.performerId) {
      return card.dataset.performerId;
    }

    return null;
  }

  /**
   * Get rating from performer card if displayed
   * @param {HTMLElement} card - Performer card element
   * @returns {number|null} Rating value or null
   */
  function getRatingFromCard(card) {
    // Try to find rating display in the card
    // Stash might display rating in various ways
    const ratingEl = card.querySelector("[class*='rating']");
    if (ratingEl) {
      const text = ratingEl.textContent;
      const match = text.match(/(\d+)/);
      if (match) {
        return parseInt(match[1]);
      }
    }
    return null;
  }

  /**
   * Check if rating widget already exists on card
   * @param {HTMLElement} card - Performer card element
   * @returns {boolean} True if widget exists
   */
  function hasRatingWidget(card) {
    return card.querySelector(".pr-star-rating") !== null;
  }

  /**
   * Inject rating widget into performer card
   * @param {HTMLElement} card - Performer card element
   */
  async function injectRatingWidget(card) {
    if (hasRatingWidget(card)) {
      return;
    }

    const performerId = getPerformerIdFromCard(card);
    if (!performerId) {
      console.warn("[PerformerRating] Could not get performer ID from card");
      return;
    }

    try {
      // Fetch current rating from API
      const rating = await getPerformerRating(performerId);
      
      // Create and inject the widget
      const ratingWidget = createStarRating(rating, performerId);
      
      // Find the best place to inject (after the image, before other content)
      const cardContent = card.querySelector(".performer-card-content") || 
                         card.querySelector(".card-section") ||
                         card.querySelector(".card-body") ||
                         card;
      
      // Try to insert at the beginning of the card content
      if (cardContent.firstChild) {
        cardContent.insertBefore(ratingWidget, cardContent.firstChild);
      } else {
        cardContent.appendChild(ratingWidget);
      }

      // Mark card as processed
      card.dataset.prProcessed = "true";
    } catch (err) {
      console.error("[PerformerRating] Error injecting rating widget:", err);
    }
  }

  /**
   * Process all performer cards on the page
   */
  async function processPerformerCards() {
    // Various selectors for performer cards in Stash UI
    const cardSelectors = [
      ".performer-card",
      "[class*='PerformerCard']",
      ".card.performer",
      ".grid-item.performer"
    ];

    let cards = [];
    for (const selector of cardSelectors) {
      const found = document.querySelectorAll(selector);
      if (found.length > 0) {
        cards = Array.from(found);
        break;
      }
    }

    // Alternative: look for cards that have performer links
    if (cards.length === 0) {
      const allCards = document.querySelectorAll(".card");
      cards = Array.from(allCards).filter(card => {
        const link = card.querySelector("a[href*='/performers/']");
        return link !== null;
      });
    }

    // Process each card (in batches to avoid overwhelming the API)
    const batchSize = 5;
    for (let i = 0; i < cards.length; i += batchSize) {
      const batch = cards.slice(i, i + batchSize);
      await Promise.all(batch.map(card => {
        if (!card.dataset.prProcessed) {
          return injectRatingWidget(card);
        }
        return Promise.resolve();
      }));
    }
  }

  // ============================================
  // PAGE DETECTION
  // ============================================

  /**
   * Check if we're on the performers page
   * @returns {boolean} True if on performers page
   */
  function isPerformersPage() {
    const path = window.location.pathname;
    return path === "/performers" || path === "/performers/" || path.startsWith("/performers?");
  }

  // ============================================
  // INITIALIZATION
  // ============================================

  /**
   * Initialize the plugin
   */
  function init() {
    console.log("[PerformerRating] Plugin initialized");

    // Initial processing if on performers page
    if (isPerformersPage()) {
      // Delay to allow Stash UI to render
      setTimeout(() => {
        processPerformerCards();
      }, 1000);
    }

    // Watch for DOM changes (SPA navigation, lazy loading, etc.)
    const observer = new MutationObserver((mutations) => {
      if (!isPerformersPage()) {
        return;
      }

      // Debounce processing
      clearTimeout(observer._timeout);
      observer._timeout = setTimeout(() => {
        processPerformerCards();
      }, 500);
    });

    observer.observe(document.body, {
      childList: true,
      subtree: true,
    });

    // Listen for Stash navigation events if PluginApi is available
    if (typeof PluginApi !== "undefined" && PluginApi.Event && PluginApi.Event.addEventListener) {
      PluginApi.Event.addEventListener("stash:location", (e) => {
        console.log("[PerformerRating] Page changed:", e.detail.data.location.pathname);
        
        if (isPerformersPage()) {
          // Delay to allow UI to render
          setTimeout(() => {
            processPerformerCards();
          }, 500);
        }
      });
    }
  }

  // Start plugin
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
