<?php

/*
 * Copyright (C) 2026 Abdelmonem Awad <eg2@live.com>
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice,
 *    this list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
 * INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
 * AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 * AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
 * OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 * SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.
 */

namespace OPNsense\LinkHealth;

use OPNsense\Base\BaseModel;
use OPNsense\Base\Messages\Message;

class LinkHealth extends BaseModel
{
    public function performValidation($validateFullModel = false)
    {
        $messages = parent::performValidation($validateFullModel);

        $general = $this->general;
        if ($validateFullModel || $general->isFieldChanged()) {
            /* The three rates are the boundaries between four verdicts that get progressively
               worse. Out of order, or equal, one of the verdicts can never be reached and a port
               would jump from clean straight to failed. */
            if ((int)(string)$general->ppm_warn <= (int)(string)$general->ppm_watch) {
                $messages->appendMessage(new Message(
                    gettext('The warning rate must be higher than the watch rate.'),
                    'general.ppm_warn'
                ));
            }
            if ((int)(string)$general->ppm_fail <= (int)(string)$general->ppm_warn) {
                $messages->appendMessage(new Message(
                    gettext('The failure rate must be higher than the warning rate.'),
                    'general.ppm_fail'
                ));
            }
            /* Counting link changes over a span shorter than the span between two readings can
               only ever see the changes of a single reading, so the threshold would be measured
               against a period that was never actually observed. */
            if ((int)(string)$general->flap_window < (int)(string)$general->poll_interval) {
                $messages->appendMessage(new Message(
                    gettext('The link change period may not be shorter than the poll interval.'),
                    'general.flap_window'
                ));
            }
        }

        foreach ($this->ports->port->iterateItems() as $port) {
            if (!$validateFullModel && !$port->isFieldChanged()) {
                continue;
            }
            /* The load test floods the port it runs on and is only offered for ports that are
               being watched, so a neighbour address on a port nobody watches is an address that
               would never be used - almost always a forgotten checkbox rather than an intention. */
            if ((string)$port->neighbour != '' && (string)$port->enabled != '1') {
                $messages->appendMessage(new Message(
                    gettext('A port that is not watched is never tested, so it needs no neighbour.'),
                    $port->__reference . '.neighbour'
                ));
            }
        }

        return $messages;
    }
}
