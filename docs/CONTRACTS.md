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

## How the cart mutation came to be observed

Written in order, because the first answer was wrong and the wrong answer is
the useful part.

### 1. Page-context hooks saw nothing

A `fetch` wrapper installed before the app's own initialization caught
`getCart` but saw nothing for the cart mutation or for My Items. An
`XMLHttpRequest` wrapper saw nothing. `performance` resource entries had no
matching entry. No service worker controlled the page.

To find out whether the mutation was observable at all, an item **already in
the cart** was incremented from quantity 2 to 3 through the site's own UI
control and then decremented back to 2, with the final state re-read from a
fresh page load. The item, its offer, price and promotions never left the cart,
so nothing could be lost by a failed restore. A remove-and-restore has none of
those safety properties and was refused.

No request was visible.

### 2. The conclusion drawn from that was wrong

This document said the mutation "runs off the main thread", and that a
browser-level capture was needed because Playwright can see worker traffic.

That was a claim about Walmart inferred from a fact about our hooks. The
evidence supported only the narrower statement: **a page-context hook did not
observe this traffic.** Silence from an instrument is a fact about the
instrument until something else corroborates it.

The cost of the stronger claim was real. It stood as the reason no mutation
work could begin, so the work did not begin, and the blocker was a wrong
diagnosis rather than a hard limit.

### 3. Browser-level events observed it, from the page

Subscribing to Playwright's own request events -- which observe at the browser
level rather than from inside the document -- recorded four `updateItems`
requests with bodies.

Playwright attributes worker traffic to the owning page, so the capture records
`from_worker` for each request to tell the two explanations apart. **It was
false for all four.** The requests came from the page. Nothing was ever off the
main thread; the hooks simply ran somewhere that could not see them.

My Items remains unobserved, and this time that is all it means. There is no
finding about how it works.

### 4. Where that leaves the contract

The request is observed and is written up under **Cart (write)** above: the
path, the persisted-query hash, the eighteen feature flags, and the absence of
both `operationName` and `lineItemId`.

The response is not observed, and neither is per-line delivery eligibility.
Both are gates in code, not cautions in prose, and the cart mutation refuses
before opening a browser while either stands.

## What the first response capture keeps

Metadata and structure, on both sides, and no bodies.

    url (scrubbed), method, resource_type, from_worker, operation, body_shape
    status, ok, body_shape

No headers. No request body, even though the request contract is already known
-- keeping it again would add the product chosen, its identifiers and its
quantity, and add no evidence. No response body. A body that will not parse is
named `<unparseable>` rather than copied.

Everything the guards need is still checked in memory before it is dropped: the
operation, the URL, the body's structure, and the correlation between a request
and its answer. Minimising what is kept must not weaken what is verified, and
a test asserts a malformed body is still refused.

## What the first response capture can and cannot tell us

The response to `updateItems` has never been observed. The capture keeps its
shape and not its body, and the shape keeps only keys from a list of field
names already seen -- the GraphQL envelope, the operation, and the fields
observed in the request.

This is a deliberate trade, and it costs discovery. A response whose interesting
field is named something nobody has seen will arrive as a count of unknown keys
rather than as a name. That is the intended outcome, not a failed run: the count
says a second, narrower observation is needed, and the second one names the
paths it will keep in advance.

The alternative was to keep any key that looks like a field name. It does not
work, and it is worth writing down why, because the reasoning is not obvious.
"Short and identifier-shaped" describes `quantity`, and it equally describes
`Eric`, `ashley`, `household1` and any short opaque token. A response keyed by
customer or by household would have written those out in full, in a file
described in its own docstring as carrying none of the contents. A pattern
cannot tell a field name from data that happens to look like one; only a list
of names we have already seen can.

So the artefact stays value-free and the discovery is incremental. Anything
wider than this needs an explicit decision from the person whose data it is,
made in advance and in those terms -- not inherited from a capture that was
approved as value-free.
