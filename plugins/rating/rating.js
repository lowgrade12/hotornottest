(function () {
  "use strict";

  // ============================================
  // RATINGS CACHE
  // ============================================
  
  // Local cache for ratings to ensure UI stays in sync across React re-renders
  // Map<performerId, { rating100: number|null, timestamp: number }>
  const ratingsCache = new Map();
  
  // Cache TTL in milliseconds (5 minutes) - after this, we'll re-fetch from server
  const CACHE_TTL = 5 * 60 * 1000;
  
  /**
   * Get a rating from the local cache
   * @param {string} performerId - Performer ID
   * @returns {number|null|undefined} Cached rating, or undefined if not cached or expired
   */
  function getCachedRating(performerId) {
    const cached = ratingsCache.get(performerId);
    if (!cached) return undefined;
    
    // Check if cache entry is still valid
    if (Date.now() - cached.timestamp > CACHE_TTL) {
      ratingsCache.delete(performerId);
      return undefined;
    }
    
    return cached.rating100;
  }
  
  /**
   * Set a rating in the local cache
   * @param {string} performerId - Performer ID
   * @param {number|null} rating100 - Rating value
   */
  function setCachedRating(performerId, rating100) {
    ratingsCache.set(performerId, {
      rating100,
      timestamp: Date.now()
    });
  }

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
   * @param {number} rating100 - Rating value (0-100)
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

    const updatedRating = result.performerUpdate.rating100;
    
    // Update the local cache so React re-renders use the correct value
    setCachedRating(performerId, updatedRating);
    
    console.log(`[PerformerRating] Updated performer ${performerId} rating to ${updatedRating}`);
    
    // Dispatch custom event so all rating widgets for this performer can update
    document.dispatchEvent(new CustomEvent("performer:rating:updated", {
      detail: { performerId, rating100: updatedRating }
    }));
    
    return result.performerUpdate;
  }

  /**
   * Get performer rating by ID
   * Uses local cache for recently updated ratings to ensure UI consistency
   * @param {string} performerId - The performer's ID
   * @returns {Promise<number|null>} Rating value or null if not rated
   */
  async function getPerformerRating(performerId) {
    // Check the cache first
    const cachedRating = getCachedRating(performerId);
    if (cachedRating !== undefined) {
      return cachedRating;
    }
    
    const query = `
      query GetPerformerRating($id: ID!) {
        findPerformer(id: $id) {
          id
          rating100
        }
      }
    `;

    const result = await graphqlQuery(query, { id: performerId });
    const rating = result.findPerformer ? result.findPerformer.rating100 : null;
    
    // Cache the fetched rating
    setCachedRating(performerId, rating);
    
    return rating;
  }

  /**
   * Get multiple performer ratings in a single request
   * Uses local cache for recently updated ratings to ensure UI consistency
   * @param {string[]} performerIds - Array of performer IDs
   * @returns {Promise<Map<string, number|null>>} Map of performer ID to rating
   */
  async function getMultiplePerformerRatings(performerIds) {
    if (performerIds.length === 0) {
      return new Map();
    }

    const ratings = new Map();
    const uncachedIds = [];
    
    // First, check the cache for each performer
    for (const id of performerIds) {
      const cachedRating = getCachedRating(id);
      if (cachedRating !== undefined) {
        ratings.set(id, cachedRating);
      } else {
        uncachedIds.push(id);
      }
    }
    
    // If all ratings were cached, return immediately
    if (uncachedIds.length === 0) {
      return ratings;
    }

    // Build a query that fetches only uncached performers
    // GraphQL aliases allow us to query the same field multiple times with different arguments
    const aliasedQueries = uncachedIds.map((id, index) => 
      `p${index}: findPerformer(id: "${id}") { id rating100 }`
    ).join("\n");

    const query = `query GetMultiplePerformerRatings { ${aliasedQueries} }`;

    try {
      const result = await graphqlQuery(query, {});
      
      uncachedIds.forEach((id, index) => {
        const performer = result[`p${index}`];
        const rating = performer ? performer.rating100 : null;
        ratings.set(id, rating);
        // Cache the fetched rating
        setCachedRating(id, rating);
      });
      
      return ratings;
    } catch (err) {
      console.error("[PerformerRating] Error fetching multiple ratings:", err);
      // Log which performers couldn't be fetched
      if (uncachedIds.length > 0) {
        console.warn(`[PerformerRating] Failed to fetch ratings for performers: ${uncachedIds.join(", ")}`);
      }
      // Return what we have from cache (may be partial)
      return ratings;
    }
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
    // Store the current rating in dataset so it can be updated
    container.dataset.currentRating = currentRating !== null ? currentRating : "";

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
          // Update the stored rating in dataset
          container.dataset.currentRating = newRating100;
          updateStarDisplay(container, newRating100);
          showRatingFeedback(container, newRating100);
          // Update any native Stash rating displays on the card
          const parentCard = findParentCard(container);
          updateNativeRatingDisplay(parentCard, newRating100);
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

    // Reset hover preview when mouse leaves - use stored rating from dataset
    container.addEventListener("mouseleave", () => {
      const storedRating = container.dataset.currentRating;
      const ratingValue = storedRating !== "" ? parseInt(storedRating) : null;
      updateStarDisplay(container, ratingValue);
    });

    // Add rating slider (range input)
    const ratingSlider = document.createElement("input");
    ratingSlider.type = "range";
    ratingSlider.className = "pr-rating-slider";
    ratingSlider.min = "0";
    ratingSlider.max = "100";
    ratingSlider.step = "1";
    ratingSlider.value = currentRating !== null ? currentRating : "0";

    // Add rating value display
    const ratingValue = document.createElement("span");
    ratingValue.className = "pr-rating-value";
    ratingValue.textContent = currentRating !== null ? currentRating : "--";

    // Handle slider input (real-time updates while dragging)
    ratingSlider.addEventListener("input", () => {
      const newRating = parseInt(ratingSlider.value);
      // Update stars and value display in real-time while dragging
      updateStarDisplay(container, newRating);
      ratingValue.textContent = newRating;
    });

    // Handle slider change (when user releases the slider - save to server)
    ratingSlider.addEventListener("change", async () => {
      const newRating = parseInt(ratingSlider.value);
      
      try {
        await updatePerformerRating(performerId, newRating);
        // Update the stored rating in dataset
        container.dataset.currentRating = newRating;
        showRatingFeedback(container, newRating);
        // Update any native Stash rating displays on the card
        const parentCard = findParentCard(container);
        updateNativeRatingDisplay(parentCard, newRating);
      } catch (err) {
        console.error("[PerformerRating] Failed to update rating:", err);
        showRatingError(container);
        // Restore the previous rating on error
        const storedRating = container.dataset.currentRating;
        const currentValue = storedRating !== "" ? parseInt(storedRating) : 0;
        ratingSlider.value = currentValue;
        ratingValue.textContent = currentValue !== 0 ? currentValue : "--";
        updateStarDisplay(container, currentValue !== 0 ? currentValue : null);
      }
    });

    // Prevent card click when interacting with slider
    ratingSlider.addEventListener("click", (e) => {
      e.stopPropagation();
    });
    ratingSlider.addEventListener("mousedown", (e) => {
      e.stopPropagation();
    });

    container.appendChild(ratingSlider);
    container.appendChild(ratingValue);

    // Prevent card click when interacting with rating
    container.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
    });

    // Listen for rating updates from other sources (e.g., other widgets for same performer)
    // Note: Global event delegation is set up in init() to avoid memory leaks

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

    // Update rating slider and value display
    const ratingSlider = container.querySelector(".pr-rating-slider");
    if (ratingSlider) {
      ratingSlider.value = rating100 !== null ? rating100 : 0;
    }
    
    const ratingValue = container.querySelector(".pr-rating-value");
    if (ratingValue) {
      ratingValue.textContent = rating100 !== null ? rating100 : "--";
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
      } else {
        // Reset non-hovered stars to empty state
        star.innerHTML = "☆";
      }
    });
  }

  /**
   * Show brief feedback after rating update
   * @param {HTMLElement} container - Star rating container
   * @param {number} rating100 - New rating value
   */
  function showRatingFeedback(container, rating100) {
    const ratingValue = container.querySelector(".pr-rating-value");
    if (ratingValue) {
      ratingValue.classList.add("pr-feedback-success");
      setTimeout(() => {
        ratingValue.classList.remove("pr-feedback-success");
      }, 500);
    }
  }

  /**
   * Show error feedback after failed rating update
   * @param {HTMLElement} container - Star rating container
   */
  function showRatingError(container) {
    const ratingValue = container.querySelector(".pr-rating-value");
    if (ratingValue) {
      ratingValue.classList.add("pr-feedback-error");
      setTimeout(() => {
        ratingValue.classList.remove("pr-feedback-error");
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
   * Update Stash's native rating display elements on the card
   * This ensures all rating displays are synchronized after a rating change
   * @param {HTMLElement} card - Performer card element
   * @param {number} rating100 - New rating value (0-100)
   */
  function updateNativeRatingDisplay(card, rating100) {
    if (!card) return;
    
    // Find and update any native Stash rating elements on the card
    // Look for common Stash rating element patterns
    const ratingSelectors = [
      ".rating-number",
      ".rating-value",
      ".rating-display",
      "[class*='rating'][class*='number']",
      "[class*='rating'][class*='value']"
    ];
    
    // Try specific selectors first for better performance
    let ratingElements = [];
    for (const selector of ratingSelectors) {
      const found = card.querySelectorAll(selector);
      if (found.length > 0) {
        ratingElements = Array.from(found);
        break;
      }
    }
    
    // Fallback to broader selector if no specific elements found
    if (ratingElements.length === 0) {
      ratingElements = Array.from(card.querySelectorAll("[class*='rating']"));
    }
    
    ratingElements.forEach(el => {
      // Skip our plugin's rating widget
      if (el.classList.contains("pr-star-rating") || 
          el.classList.contains("pr-rating-input") ||
          el.closest(".pr-star-rating")) {
        return;
      }
      
      // Update the text content if it contains a valid rating number (0-100)
      const text = el.textContent.trim();
      // Match numbers that could be ratings (1-3 digits, standalone or at start/end)
      const match = text.match(/^(\d{1,3})$|^(\d{1,3})\/|\/(\d{1,3})$/);
      if (match) {
        // Get the matched number (from any of the capture groups)
        const matchedNumber = match[1] || match[2] || match[3];
        const numericValue = parseInt(matchedNumber, 10);
        // Only update if the number is in valid rating range
        if (numericValue >= 0 && numericValue <= 100) {
          el.textContent = text.replace(matchedNumber, rating100.toString());
        }
      }
    });
  }

  /**
   * Find the parent card element containing the rating widget
   * @param {HTMLElement} container - The rating widget container
   * @returns {HTMLElement|null} The parent card element or null
   */
  function findParentCard(container) {
    // Walk up the DOM tree to find the performer card
    let element = container.parentElement;
    while (element) {
      // Check for common card class names first (fast)
      if (element.classList.contains("performer-card") ||
          element.classList.contains("card")) {
        return element;
      }
      // Check for direct link as child (faster than querySelector)
      const children = element.children;
      for (let i = 0; i < children.length; i++) {
        const child = children[i];
        if (child.tagName === "A" && 
            child.href && 
            child.href.includes("/performers/")) {
          return element;
        }
      }
      element = element.parentElement;
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
   * @param {number|null} rating - Pre-fetched rating value (optional)
   */
  async function injectRatingWidget(card, rating = undefined) {
    if (hasRatingWidget(card)) {
      return;
    }

    const performerId = getPerformerIdFromCard(card);
    if (!performerId) {
      console.warn("[PerformerRating] Could not get performer ID from card");
      return;
    }

    try {
      // Fetch current rating from API if not provided
      if (rating === undefined) {
        rating = await getPerformerRating(performerId);
      }
      
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

    // Filter to only unprocessed cards
    const unprocessedCards = cards.filter(card => !card.dataset.prProcessed);
    if (unprocessedCards.length === 0) {
      return;
    }

    // Collect performer IDs for batch query
    const cardIdMap = new Map(); // performerId -> card
    for (const card of unprocessedCards) {
      const performerId = getPerformerIdFromCard(card);
      if (performerId) {
        cardIdMap.set(performerId, card);
      }
    }

    const performerIds = Array.from(cardIdMap.keys());
    if (performerIds.length === 0) {
      return;
    }

    try {
      // Fetch all ratings in a single batch query
      const ratings = await getMultiplePerformerRatings(performerIds);
      
      // Inject widgets for each card in parallel
      const widgetPromises = Array.from(cardIdMap.entries()).map(([performerId, card]) => {
        const rating = ratings.get(performerId);
        return injectRatingWidget(card, rating);
      });
      await Promise.all(widgetPromises);
    } catch (err) {
      console.error("[PerformerRating] Error processing cards in batch:", err);
      // Fallback to individual processing (also parallelized)
      await Promise.all(unprocessedCards.map(card => injectRatingWidget(card)));
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

  // Debounce timeout for mutation observer
  let processingTimeout = null;

  /**
   * Initialize the plugin
   */
  function init() {
    console.log("[PerformerRating] Plugin initialized");

    // Global event listener for rating updates (event delegation pattern)
    // This avoids memory leaks from per-widget listeners
    document.addEventListener("performer:rating:updated", (e) => {
      const { performerId, rating100 } = e.detail;
      // Find all rating widgets for this performer
      const widgets = document.querySelectorAll(`.pr-star-rating[data-performer-id="${performerId}"]`);
      widgets.forEach((container) => {
        container.dataset.currentRating = rating100 !== null ? rating100 : "";
        updateStarDisplay(container, rating100);
        // Update native Stash rating displays on the associated card
        const parentCard = findParentCard(container);
        updateNativeRatingDisplay(parentCard, rating100);
      });
    });

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
      clearTimeout(processingTimeout);
      processingTimeout = setTimeout(() => {
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
