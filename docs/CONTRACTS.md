# Observed Walmart contracts

Everything here was observed in a signed-in session with read-only instrumentation,
except one deliberately reversible cart experiment noted at the bottom. Nothing was
inferred. If a shape is not written down here, we have not seen it, and code that
depends on it should say so rather than guess.

No session values appear in this file. Cart ids, pagination cursors, store ids,
postal codes and device identifiers are session-scoped: read them live, never
persist them, never commit them.

## The general pattern

Walmart's web app is Next.js (pages router). Several pages server-render their data
into `window.__NEXT_DATA__`, which means one ordinary navigation gives us the whole
payload with no persisted GraphQL hash, no synthesized headers, and no second request
to be throttled or blocked for. Prefer that path.

Pages that do **not** server-render their data fetch it client-side, and some of those
fetches happen off the main thread where page-level instrumentation cannot see them.

| Surface | Server-rendered? | Where the data lives |
| --- | --- | --- |
| Search | yes | `props.pageProps.initialData.searchResult` |
| Item | yes | `props.pageProps.initialData.data.product` |
| Order history | yes | `props.pageProps.phRedesignInitialData.data.purchaseHistory` |
| Order detail | yes | `props.pageProps.initialData.data.order` |
| Cart | no | client fetch of `getCart` |
| My Items | no | not observable from page context |

## Search

Server-rendered at `/search?q=...`. `props.pageProps.initialSearchQueryVariables` also
carries the exact variables the page would have sent, which is how the request below
was recovered.

Results are at `initialData.searchResult`. Read `itemStacks[N].itemsV2 || itemStacks[N].items`:
`itemsV2` can be `null` while `items` holds the products, and reading only the first
reports an empty catalog for a full page. Pagination is `hasMorePages` and
`paginationV2.maxPage`.

Item fields: `usItemId`, `offerId`, `name`, `sellerId`, `sellerName`, `sellerType`
(observed `null` here), `fulfillmentType`, `fulfillmentSummary[]`, `availabilityStatus`,
`availabilityStatusV2{display,value}`, `priceInfo`, `price` (number), `canAddToCart`,
`isOutOfStock`, `snapEligible`, `salesUnit`, `salesUnitType`, `weightIncrement`,
`averageWeight` (`null` on non-weighted items — this is the weighted-produce signal),
`imageInfo`, `canonicalUrl`.

Prices: `priceInfo.priceDetails.priceLines[]`, each with a `lineType` of
`CURRENT_PRICE`, `COMPARISON`, `DISCOUNTED_PRICE` or `UNIT_PRICE`, and a `values[]`
of `{key, value}` where `key` is `PRICE`, `WAS_PRICE` or `UNIT_PRICE`.

Fulfillment: `fulfillment` is `DELIVERY` or `PICKUP`; `fulfillmentMethods` is
`SCHEDULED` or `UNSCHEDULED`.

First-party is `sellerName == "Walmart.com"` **and** `fulfillmentType == "STORE"`.
`sellerType` is null in search responses, so it cannot be the test, and either
remaining signal alone can be wrong.

### Fallback transport

`GET /orchestra/snb/graphql/Search/<persisted-hash>/search?variables=<url-encoded JSON>`.

The hash is version-pinned to a Walmart web build and rotates; treat a missing
persisted query as `contract_stale`, not as a parser bug. Flags in `variables` are
copied verbatim from observed traffic — notably `enableSellerType:false`, which is
*why* `sellerType` is null in the response the parser was built against. Flipping a
flag changes the response shape.

`storeSlotBooked` is `""` and `additionalQueryParams.translation` is `null` in
ordinary signed-in traffic. Store scoping does not travel in variables; it travels in
session cookies (`assortmentStoreId`, `isoLoc`, `hasLocData`, `CID`). Send the empty
string rather than omitting the key: a missing field and an empty one are different
requests.

## Item

`https://www.walmart.com/ip/<slug>/<usItemId>`, server-rendered, at
`initialData.data.product`.

Adds what search does not have: `orderLimit`, `orderMinLimit`, `isSubstitutionEligible`,
`fulfillmentOptions[]`, `variantCriteria[]`, `variantsMap`, and a populated
`sellerType`. Because search reports `sellerType` as null, the item page is the
authority when we must be certain an offer is first-party before adding it.

`priceInfo` here is a different, richer shape than search's — `currentPrice`,
`basePrice`, `wasPrice`, `comparisonPrice`, `unitPrice`, `dropPrice`, `savings`,
`priceDisplayCodes`. Do not reuse the search price parser.

Unobserved: a populated variant family. The item sampled had `variantCriteria: []`
and an empty `variantsMap`.

## Cart (write)

One operation does all four verbs: `POST /orchestra/cartxo/graphql/updateItems/<hash>`
(also served under `/orchestra/home/`). Observed by capturing a real session, four
requests; see DESIGN.md for how and why.

`variables.input = {cartId, items:[{offerId, usItemId, quantity, name,
isPharmacyPrescription, isQSRItem}], enableLiquorBox, skipPolicyCheck, cartLeanMode,
enableCartSplitClarity, features:[18 opaque flags]}`.

**Quantity is absolute on the wire, and `0` is removal.** The CLI exposes only deltas;
the absolute number is derived inside one worker action from a cart read taken moments
earlier. Nothing outside `cart_mutate.py` can set a quantity outright.

**The persisted hash is pinned** and rotates with Walmart's web builds. When it stops
being recognised every mutation fails the same way, reported as `contract_stale`
rather than as a mystery.

**The response shape is UNOBSERVED, and that gates the feature.** The capture recorded
requests only. There is therefore no honest definition of "Walmart accepted this", so
`ACCEPTED_RESPONSE_SHAPE` is `None`, `acknowledgement()` can never return accepted, and
the action refuses before opening a browser. Filling that constant in requires a
captured response, not a decision.

Even once it is known, a response is an acknowledgement and never evidence of the new
state: success will still require a fresh cart read showing the target.

**Cart-level `fulfillment.intent` is all we have for delivery eligibility.** There is
no observed per-line delivery signal, so the guard checks the cart's intent and the
cart's `storeId` against the pinned store. That is a weaker claim than "this line can
be delivered", and it is one reason mutation stays gated.

## Cart (read)

Not server-rendered. The page issues
`GET /orchestra/home/graphql/getCart/<persisted-hash>?variables=<json>` with
`variables.cartInput = {cartId, forceRefresh, enableLiquorBox, enableCartSplitClarity,
features:[...]}` plus a large set of feature booleans.

We read the cart by navigating to `/cart` and listening for the page's own response
rather than replaying the request. Two reasons: a replay with a partial header set was
answered `429` twice while the page's identical request succeeded, and listening means
the read never holds the cart id at all.

**The invariant, restated.** "We never hold the cart id" was true of every path that
existed when it was written, and `updateItems` requires it — so the claim had to
narrow rather than quietly acquire an exception. The rule is now that the cart id
**never crosses the worker boundary and never survives the action**: read from the
live cart inside a single Python action, used in the request it was read for, and gone
when that action returns. It is never sent over NDJSON, never persisted, never cached
between actions, never logged, never placed in an error message, and never revealed by
`--verbose`. What does not cross a boundary cannot leak past it.

`scrub.SENSITIVE_KEYS` covers `cartId`, because a capture on disk was the one place it
could quietly outlive that claim. It did, in the first real capture, until a review
asked.

`data.cart`: `id`, `lineItems[]`, `priceDetails`, `fulfillment`, `customer`,
`cartCustomerContext`, `checkoutable`, `checkoutableErrors[]`, `checkoutableWarnings[]`,
`errorLineItems[]`, `operationalErrors[]`, `itemFulfillmentTypes[]`, `isWicEligible`.

Line item: `id`, `quantity`, `quantityLabel`, `quantityString`, `priceInfo{itemPrice,
wasPrice, unitPrice, linePrice, savedPrice, tierPrice, priceChange, priceDisplayCodes}`,
`priceInfoDetails`, `product`, `fulfillmentSourcingDetails`,
`cartAvailableFulfillmentOptions[]`, `subscription`, `createdDateTime`.

`lineItem.product`: `usItemId`, `offerId`, `name`, `brand`, `sellerId`, `sellerName`,
`sellerDisplayName`, `sellerType`, `catalogSellerId`, `fulfillmentType`,
`fulfillmentOptions[]`, `availabilityStatus`, `salesUnit`, `salesUnitType`,
`weightIncrement`, `orderLimit`, `orderMinLimit`, `isSubstitutionEligible`, `isAlcohol`,
`snapEligible`, `imageInfo`, `returnPolicy`, `category`, `departmentName`.

Read `orderLimit` and `orderMinLimit` rather than assuming them. Never derive totals
from the lines: a total we computed is not the total Walmart will charge.

**Unobserved:** the empty-cart response. We do not know whether an empty cart returns
`lineItems: []` or a null `cart`, so unreadable and empty must not collapse into each
other — reporting an unreadable cart as empty is how someone shops twice. Settle this
on a genuinely empty cart or a throwaway account, never by emptying a real one.

## Order history, including in-store receipts

Server-rendered at `/orders`, at `props.pageProps.phRedesignInitialData.data.purchaseHistory`:
`orders[]` (5 per page), `pageInfo{nextPageCursor, prevPageCursor}`, `filterGroups[]`,
`activeGroupsCount`.

In-store receipts are in the same list: each order has `type` of `GLASS` or `IN_STORE`
and a boolean `isInStore`. The archive and the receipts feature are one traversal.

Order: `id`, `displayId`, `orderDate`, `title`, `shortTitle`, `type`, `isInStore`,
`itemCount`, `groups[]`, `priceDetails`, `paymentMethods`, `actions`, `timezone`.

Group: `groupId`, `orderId`, `purchaseOrderId`, `status`, `items[]`, `itemCount`,
`store`, `seller`, `deliveryType`, `fulfillmentType`, `deliveredDate`,
`orderGroupDates`, `shipment`, `subGroups`, and people fields — `driver`, `shopper`,
`pickedBy`, `checkInDetails`.

The archive must drop `paymentMethods`, addresses, and the people fields, with tests
proving a fixture containing them produces output without them. The archive needs what
was bought, not who carried it.

Pagination is cursor-based. `/orders?cursor=<value>` is **not** the mechanism — it
returns page one. The "Next page" control issues
`GET /orchestra/cph/graphql/PurchaseHistoryV3/<persisted-hash>?variables=<json>` with
`input.cursor` from `pageInfo.nextPageCursor`, `limit: 5`, `search: ""`,
`isStrictSearch: false`, `type`/`minTimestamp`/`maxTimestamp` all `null`, and
`platform: "WEB"`.

`limit` is 5 because that is what the page sends. Raising it is an experiment with its
own observation, not an assumption. Because the timestamp filters are null in real
traffic, the default two years and `--all` are ours to implement by walking cursors
until `orderDate` passes the cutoff. A guessed timestamp filter that silently returns
nothing looks exactly like having no old orders.

### Order detail

The list rows are an index, not a record. `groups[].items[]` there carries only `id`,
`name`, `offerId`, `quantity`, `imageInfo`, `statusCode`, `isUnavailable`, `uniqueId` —
no `usItemId` and no price. Archiving from the list alone would record what was bought
but neither what it cost nor a stable catalog id, and `offerId` is a weaker join key
than `usItemId` for bought-before annotations.

Server-rendered at `https://www.walmart.com/orders/<orderId>` (`/orders/[orderId]`),
at `props.pageProps.initialData.data.order`.

**The fulfillment groups are under a dynamic key.** On the order observed they were
under `groups_2101`. Find them by the `groups_` prefix, never by literal name, and
handle more than one such key rather than taking the first.

`order`: `id`, `displayId`, `orderDate`, `shortTitle`, `itemCount`, `timezone`,
`martId`, `groups_<n>[]`, `priceDetails`, and the sensitive material listed below.

`item`: `id`, `quantity`, `priceInfo{linePrice}`, `productInfo{usItemId, offerId, name,
shortDescription, imageInfo, isAlcohol, salesUnitType}`, `conditionGroupCode`,
`orderedSalesUnit`, `requestedWeight`, `addOns`, `actions`. `requestedWeight` and
`orderedSalesUnit` are the weighted-produce fields on the order side; both were null
on the item observed, so their populated shape is unobserved.

`priceDetails`: `subTotal`, `taxTotal`, `grandTotal`, `grandTotalWithTips`,
`strikethroughSubTotal`, `savings`, `allSavings`, `discounts`, `fees`,
`belowMinimumFee`, `minimumThreshold`, `memberShippingSaving`, `driverTip`,
`donations`, `refund`, `rewards`, `authorizationAmount`, `monthlyPayment`,
`summaryDescription`. Store these as given.

The detail page, not the list, is where the sensitive material lives. Drop at minimum
`paymentMethods`, `refundPaymentMethods`, `chargeHistory`, `authorizationAmount`,
`customer`, `idBarcodeImageUrl` (a scannable in-store identifier),
`orderInvoiceDetails`, `digitalInvoiceDetail`, and within each group `deliveryAddress`,
`deliveryInstructions`, `deliveryPreferences`, `alternatePickupPerson`,
`alternateGroupRecipients`, `callDriverDetails`, `checkInDetails`, `accessPointId`,
`driver`, `shopper`, `pickedBy`. Assert the denylist at any depth: a nested
`deliveryAddress` is the one that survives a shallow check.

Suggested traversal: walk `/orders` for the index, which is cheap and carries
`type` and the cursor, stop on `orderDate` past the cutoff, then fetch each surviving
order's detail page once and archive from that, keyed by order id, so a re-sync
dedupes instead of refetching.

## Bot protection and throttling

A `412` is **not** by itself a bot block — Walmart also uses it for ordinary
precondition failures. The payload decides. A PerimeterX block body carries
`blockScript`, `jsClientSrc`, `firstPartyEnabled`, `hostUrl`, `appId`, and a
`redirectUrl` to `/blocked`, plus `uuid`/`vid` visitor identifiers that our scrubber
must strip. Landing on the interstitial is also a block even when the body is
ordinary HTML.

A `429` is a throttle, not a block, and the two must stay distinguishable. Neither is
ever retried automatically, and no challenge is ever automated, simulated or bypassed.

## Not observable from page context

The cart mutation and My Items both run off the main thread: a `fetch` wrapper
installed before the app's own initialization catches `getCart` but sees nothing for
either of these, an `XMLHttpRequest` wrapper sees nothing, `performance` resource
entries have no matching entry, and no service worker controls the page. Playwright
observes at the browser level and can see worker traffic, so these need a
browser-level capture.

Until then there is no cart mutation code. `home/updateItems` is a name and a verb;
we have no request body, no response body, and no idea what a partial success looks
like. Verification of post-state is the whole authorization, so `cart show` comes
first.

### The one mutation performed

To learn whether the mutation was observable at all, an item **already in the cart**
was incremented from quantity 2 to 3 through the site's own UI control and then
decremented back to 2, with the final state re-read from a fresh page load. The item,
its offer, price and promotions never left the cart, so nothing could be lost by a
failed restore. The result was negative — no request was visible — which is what put
this section here.

A remove-and-restore has none of those safety properties and was refused.
